# Changelog

All notable changes to AlphaLab are documented in this file.

This project follows the principles of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to Semantic Versioning.

---

# [2.13.0] - 2026-09-12

**Durable Run State + the Run-State Store.**

`capture` / `restore` have covered `ExecutionPipelineState`, `SessionState` and
`BacktestState` since v2.9, and `StrategyStateProtocol` closed the last
precondition in v2.10. A run could be described completely and rebuilt exactly.
It could not be **put anywhere**.

Measured at v2.12: zero filesystem calls in `alphalab` — the one I/O call in the
package is `urllib.urlopen`, in `marketdata/transport.py`. `MemoryStorage` was
the only `PersistenceProtocol` implementation, held everything in memory, had no
run identity, no sequence, and no decoder for its own `PersistenceState`. And no
production module imported `runtime.snapshot`, `runtime.session_snapshot` or
`backtesting.snapshot` at all: their only callers were tests. The one object in
the tree that claimed to own recovery — `production.Checkpoint` with
`RecoveryEngine` — held six opaque caller-supplied strings and restored nothing.

The gap was not serialization, not schema, and not a missing restore boundary.
**Durability had no owner.**

There was also a defect underneath it. `MemoryStorage._create_id()` calls
`new_id()`, which reads the ambient identifier source, so every storage
operation drew from whatever stream was installed. Inside a run's `id_scope`
that is the *run's* stream: one `save_snapshot` plus one `append_event` advanced
`draws` **0 → 2**, and the two identifiers it took were the run's own next two —
stamped onto a `SnapshotSaved` and an `EventAppended` while the run skipped to
its fourth and fifth. `load_snapshot` drew as well, so a durable store built on
that protocol could not have *read a run back* without changing it.

The shape of the answer was already in the repository. `marketdata/transport.py`
diagnosed the same class of problem — "silently fake data with no seam to ever
make it real" — and answered it with a narrow `Transport` protocol, a real
`HttpTransport`, and a `StaticTransport` a caller constructs by name. This is
that shape, applied to persistence. See **ADR-0029**.

## Added

- **`RunStateStore`** — the one owner of durable run state, in
  `alphalab.persistence`. Four methods over `(run_id, sequence) → payload`:
  `put`, `get`, `latest`, `list_runs`. **Payload-agnostic**: it moves a `str`,
  imports no snapshot, runtime, session, backtesting, portfolio, OMS or strategy
  module, and never decodes what it holds. There is no `delete`, no `append`, no
  query language and no transaction, because nothing here needs one.
- **`FileRunStateStore`** — the real local backend. Standard library only, so
  `dependencies = []` stays true. Writes atomically (temporary file in the
  destination directory, `fsync`, then `os.replace`), records a SHA-256 digest
  and **verifies it before any decoder runs**. The root must already exist and be
  writable; a missing root, a root that is a file, and an unwritable root each
  **raise**. It never falls back to memory.
- **`MemoryRunStateStore`** — an explicitly named deterministic double, the
  `StaticTransport` of this boundary. Same contract, same refusals, same
  ordering. A caller constructs it by name; nothing selects it automatically and
  `FileRunStateStore` never degrades into it.
- **`RunStateRef(run_id, sequence)`** — an **identity, not a location**. Renders
  `run_id@sequence` on the separator `alphalab.lifecycle.identity` already uses,
  and refuses an empty `run_id`, a `run_id` containing `"@"`, a non-integer
  sequence and a negative one. It carries no URI, no path, no checksum and no
  byte size: a backend's addressing stays inside that backend, so a second
  backend would change no caller. Deliberately *not* shaped like `ArtifactRef`,
  which describes bytes AlphaLab never holds.
- **`RUN_STATE_ENVELOPE_SCHEMA = 1`** — the only new schema constant. A
  module-local literal, versioning what the store records *about* a payload and
  nothing inside it.
- **`examples/13_durable_run_state.py`** — a run stopping after five records,
  crossing to a separate interpreter, finishing six more, and comparing
  byte-identical against a run that never stopped.
- **ADR-0029**, *The Run-State Store and the Durability Boundary*.

## Changed

- **Persistence draws no identifier from the run's stream.** A complete
  `put` / `get` cycle inside `id_scope` advances `draws` by **exactly zero** —
  on both backends, on every method in isolation, and on the refusal paths where
  a stray identifier would otherwise hide. Neither store module calls `new_id`,
  and a structural test keeps it that way. This is what makes a mid-run
  checkpoint safe in a seeded run.
- **`alphalab.common.serialization` resolves `__serializable__` on the type**
  rather than through a `runtime_checkable` protocol check. Profiled on a
  1,600-event snapshot, that one `isinstance` was ~60% of `serialize`:
  1,827,034 calls into `inspect._shadowed_dict` and 557,008 protocol checks,
  against 0.19s actually spent encoding JSON. Measured against the pre-change
  implementation kept verbatim as an oracle: **698.0 ms → 188.8 ms, 3.70×, for a
  byte-identical 6,706,275-byte payload.** Every snapshot in the repository is
  faster — portfolio, OMS, allocation, lifecycle, pipeline, session, backtest.
  The one behavioural difference is recorded rather than glossed: a
  `__serializable__` set on an *instance* is no longer consulted, only one
  declared on a class. Dunder lookup conventionally goes through the type, and
  all seven declarations here are `def` at class scope.
- **`production.Checkpoint` and `RecoveryEngine` now say what they do.**
  Documentation only — no behaviour change, nothing removed, nothing aliased.
  Their state fields are opaque strings this package never decodes, `recover`
  restores nothing, and the docstrings now name `RunStateStore` as the boundary
  that does.

## Deprecated

- **The original nine-module persistence store** — `protocol`, `storage`,
  `state`, `engine`, `adapter`, `snapshot`, `views`, `validation`, `events` —
  is deprecated, and **removed in v3.0**. Measured at v2.12 it had **zero**
  production importers.
- **The codec spine remains canonical and is untouched**: `serializer`,
  `decode` and `exceptions` are imported by all seven snapshot modules and are
  not deprecated.
- The notice fires on **use of a deprecated name through the package**, not on
  importing the package, through the PEP 562 mechanism
  `alphalab.common.CommonEvent` already uses and for the same reason: an
  import-time warning here would fire on every consumer of `serialize` and
  `decode` — that is, on the whole execution path — to deprecate names that path
  never touches. `from alphalab.persistence.storage import MemoryStorage` stays
  silent.
- **Nothing is removed in v2.13.** These modules keep their behaviour, their
  signatures and their tests for the whole of v2.x. `RunStateStore` is not a
  renamed `PersistenceProtocol`: it has different addressing, a different unit
  of storage and a different identifier contract.

## Fixed

- Nothing. No defect in shipped behaviour was corrected by this release. The
  identifier consumption described above is a property of the deprecated store,
  which is characterized rather than repaired — see
  `tests/regression/test_persistence_draws_from_the_run_stream.py`.

## Compatibility

**No existing schema constant moves.** `PIPELINE_SNAPSHOT_SCHEMA` stays 2;
`SESSION_SNAPSHOT_SCHEMA`, `BACKTEST_SNAPSHOT_SCHEMA`,
`ALLOCATION_SNAPSHOT_SCHEMA`, `OMS_SNAPSHOT_SCHEMA` and
`LIFECYCLE_SNAPSHOT_SCHEMA` stay 1; `PORTFOLIO_SNAPSHOT_SCHEMA` stays 2;
`DEFAULT_SCHEMA_VERSION` stays 1. Only `RUN_STATE_ENVELOPE_SCHEMA = 1` is new,
and it is new, so it has nothing to be compatible with — no legacy shape, no
migration, no "missing means 1".

A v2.12 serialized payload is byte-for-byte what the store holds: the envelope
wraps, and never rewrites, re-encodes or reorders. **The run identity is not
persisted in any captured state** — it is supplied by the caller at `put`, which
is what keeps every snapshot schema still. `ArtifactRef` is unchanged and not
moved. `capture` / `restore` ownership is unchanged: the module that owns a state
still owns its projection, and no state class grew a method.

## Verified

- **Cross-process continuation.** A seeded run processes five of eleven records,
  captures, serializes and stores; a **separate interpreter** — a fresh
  `sys.executable`, not a fork, told everything in JSON and nothing by pickle —
  reads it back, restores against newly constructed runtime objects, resumes and
  processes the remaining six. The final serialized payload is **byte-identical**
  to an uninterrupted eleven-record run. ADR-0023's Class-1 comparison and the
  identifier list are asserted alongside it as failure localization, not as a
  replacement for the oracle.
- **The strategy's memory crosses too.** The workload's strategy trades on the
  parity of a counter it owns, so a child that restored the payload but not the
  strategy state would trade the wrong records and fail loudly rather than pass
  quietly.
- 3,190 tests, `mypy --strict` over 903 source files, `ruff` clean, wheel and
  sdist built and `twine check`ed.

## Explicitly not in this release

**No FX and no multi-currency valuation** — valuing across currencies still needs
a rate source that does not exist here, and v2.12's refusals stand.
**No `ArtifactStore` and no artifact-byte backend** — nothing in the tree
produces artifact bytes; `reporting.export_json`, `export_csv` and
`export_markdown` all return `str`. `ArtifactRef` continues to record where bytes
live without AlphaLab ever reading them.
**No cloud or vendor-specific storage.** **No streaming.** **No live venue
transport.** **No governance/RBAC implementation** — ADR-0018's actor record
stays deferred, because it moves a persisted lifecycle schema and is batched with
ADR-0017's evidence provenance. **No broad runtime rewrite** — the archaeology
established that the final persistence boundary does not require one, and
`ExecutionPipeline`, `TradingSession` and `BacktestEngine` are untouched.

Also deferred, deliberately: replay resumability (`ReplayState` has no snapshot
and the replay cursor drives a second identifier stream); recording live-object
*parameters* rather than class names, which would move the pipeline schema;
trimming the payload, of which 75.2% is engine event logs that no engine
*decision* reads but which ADR-0023's Class 1 includes; and incremental or
event-sourced checkpoints.

**Runtime unification remains the next architectural seam.** ADR-0023 decision 1
records that `SessionState` and `BacktestState` are the layer a future
integrated-runtime release is expected to reshape, and split the snapshot
envelopes so that reshape can move their two constants without versioning the
stable pipeline core. This store is built so that reshape cannot reach it: it
names neither state, holds only a `str`, and is addressed by an identity the
caller supplies.

## Performance

Persistence is an explicit caller action **between completed steps**, never
per event, and v2.13 adds no work to the execution path — no store symbol appears
anywhere in `alphalab.runtime`, `alphalab.backtesting`, `alphalab.strategy` or
`alphalab.replay`, and there is deliberately no append-per-event API. The reason
is measured: one capture plus serialize of a 1,600-event run costs 1.43s against
0.40s for the run itself and produces 13.4MB, so capturing per event would make a
run quadratic in its own length. A run that never persists is bit-for-bit the
v2.12 run.

Checkpoint cost is unchanged as a design position and is linear in state size —
2.9×–3.9× the run that produced it, across 100 to 6,400 events. v2.13 makes
persistence real without making it cheap; the `_convert` change reduces the
constant by 3.70× on every payload.

---

# [2.12.0] - 2026-09-12

**The Currency Authority.**

ADR-0016 made currency one of the four fields `asset_id` is derived from, so the
registry has held an authoritative, immutable answer for every registered
instrument since v2.7: changing a currency derives a different identifier, and
the classification write cannot reach it. The execution path never asked.
`_instruction` stamped `ExecutionPipelineConfig.currency` onto every
`OrderInstruction`, `ExecutionReport.currency` copied it, and
`_apply_report_to_portfolio` booked `Position.currency` from it. A
EUR-registered instrument traded on a USD pipeline produced:

```text
InstrumentRecord("SAP", EQUITY, "XETR", "EUR")   # the registry's answer
ExecutionReport.currency = "USD"                 # from the config
Position.currency        = "USD"                 # a position in no currency
```

No error, no warning, nothing downstream able to tell. ADR-0019 recorded that as
intended, and at the time it was — v2.7 had only just introduced the registry
and nothing on the execution path read it. v2.11 changed that: it threaded the
registry onto the pipeline and read it on the fill path for sector. The distance
between "this run does not know the instrument's currency" and "this run knows
it and ignores it" is the whole of this release.

Two further facts, each verified rather than inferred. `RoutingConfig.currency`
is a **fifth** currency site — ADR-0019 named three, `NormalizationPolicy` is a
fourth — defaulting to `"USD"` and compared to nothing, so a live sell against a
mismatched routing config booked a foreign position and foreign cash silently,
leaving a book whose next valuation raised mid-run. And a mixed book was already
fatal one event later: `_process_requests` snapshots the portfolio at the end of
every event, so funding a second currency onto a running pipeline raises
`MixedCurrencyValuationError` out of `process_quote` immediately.

## Added

- **`SettlementRefusal`**, reported on `ExecutionPipelineResult.settlement_refusals`
  — one per refused request, naming the instrument, its currency, this
  pipeline's settlement currency, and the fix. Derived and never persisted: no
  snapshot carries an `ExecutionPipelineResult`, and the field is appended after
  `valuation` so positional construction keeps working. An aggregated durable
  record in the manner of `UnpricedAsset` would move the pipeline schema, and is
  deferred to the release that moves it anyway.
- **`assert_single_currency_book(cash, positions, base_currency)`** — the one
  implementation of the mixed-book rule, written over the two components so that
  callers holding a ledger and a mapping reach the same rule and the same
  message as callers holding a `PortfolioState`. `assert_single_currency` keeps
  its signature and delegates.
- **ADR-0028**, *Currency Authority and the Settlement Boundary*.

## Changed

- **The instrument's currency decides what a run may trade.** Two seams, because
  there are two questions. **Seam 1**, in `_process_requests`, asks whether this
  run may *trade* an instrument, needs the registry to answer, and **drops** the
  request before the OMS — retiring both allocation ledgers through the existing
  `_retire_dropped_request`, so no reservation leaks and no contribution is
  orphaned. **Seam 2**, in `_apply_report_to_portfolio`, asks whether a report is
  denominated in what the pipeline *settles*, needs no registry, and **raises**
  `RuntimeValidationError`. Neither subsumes the other: with only Seam 2 a
  foreign instrument still slips through carrying the settlement currency, and
  with only Seam 1 a venue report never passes a check at all.
- **The dispositions differ because the vocabularies do.** The pipeline may
  decline to create an order; it may not decline a fill that already happened at
  a venue. This is the distinction `_close_unfilled_order` and `_terminate_order`
  already draw. Raising at Seam 1 would kill a live session over one instrument;
  dropping at Seam 2 would discard a real execution.
- **Seam 1 runs after the existing unpriced check**, so an instrument that is
  both foreign and unpriced is still reported as `REGISTERED_BUT_UNPRICED`.
- **`RoutingConfig.currency` is constrained** at Seam 2. The field and
  `execution_report_from_broker`'s signature are unchanged; the default `"USD"`
  is now a loud refusal on a non-USD pipeline rather than silent corruption.
- **`NAVCalculator.calculate`, `PortfolioValuation.portfolio_value` and
  `_risk_exposure` refuse a mixed book**, discharging ADR-0020 decision 5 —
  earlier than it expected, on measurement rather than on a rate source. That
  also closes `sector_exposure`, which v2.11 added and which bucketed signed
  market value with no regard for what each position traded in.
  `_risk_exposure`'s check folds into the pass it already makes.
- **`long_value` and `short_value` deliberately do not refuse.** They name no
  base currency, return an unlabelled sum, and cannot be told what to refuse
  against — a `Mapping[str, Position]` carries no account. They are components
  of a valuation, not valuations, and their only production caller is `snapshot`,
  which guards first and is the thing that attaches the label. The three-way rule
  — aggregates *and* names a currency, aggregates only, or looks up only — is the
  one the code already followed; v2.12 names it, documents it, and pins it
  against the signatures.
- **Corrected documentation.** "Holding and booking in a foreign currency is
  supported" described `PortfolioEngine` used standalone and was carried as
  though it described a pipeline run, which it never did. A stale ROADMAP bullet
  claiming strategies still do not see the marked portfolio — contradicted by
  v2.10 and by the entry above it — is marked done.

## Not changed

`STRICT_MATCH` is the only settlement rule and there is **no policy object**: a
permissive mode could only book honestly, making the book mixed so the next
snapshot raises, or convert, which is FX. `_instruction` still reads
`config.currency`, because under STRICT_MATCH that *is* the instrument's
currency for anything that trades — `Position.currency` follows
`InstrumentRecord.currency` by an equality the refusal enforces, not by a second
registry read. Nothing is added to `TradeRecord`, `OrderRequest` or `oms.Order`:
currency is an identity input and is already frozen by identity, unlike sector.
`PortfolioEngine` is not narrowed and still books any currency it is given.
`_currency_of` mirrors `_sector_of` exactly — one keyed `record_for`, no scan, no
identifier draw — and the authority is **opt-in**: a run with `instruments=None`
behaves exactly as v2.11.

**No FX rate source, no conversion, no triangulation, no multi-currency
aggregation.** A mixed book is still refused, not valued. `CURRENCY_QUANT` stays
`0.01`. No per-currency breakdown was added to `PortfolioValuationSnapshot`:
under STRICT_MATCH a pipeline book holds one currency and a mixed book is
refused before any figure is computed, so it could only ever restate `equity`.

## Compatibility

Two deliberate behavioural breaks, both correctness fixes: a run that silently
mis-booked a foreign instrument now drops the request, and a venue report
disagreeing with settlement is refused. Every position the first removes was
mislabelled; every book the second prevents was one whose next valuation would
have raised.

Measured against v2.11.0 in a separate checkout, with module provenance asserted
by path:

- all seven snapshot schema constants unchanged — `PIPELINE=2` `READABLE=(1,2)`,
  `SESSION=1`, `BACKTEST=1`, `PORTFOLIO=2`, `OMS=1`, `ALLOCATION=1`;
- a v2.11 payload restores and re-captures byte-identically;
- a single-currency run is byte-identical: same fills, same equity, same
  `sector_exposure`, same serialized snapshot SHA-256;
- identifier draws identical — 10,001 at 1,000 events and 40,001 at 4,000;
- `asset_id` derivation and `canonical_key` unchanged;
- no migration, and no migration framework.

Before implementation, instrumenting `_apply_report_to_portfolio` across the
whole v2.11 suite found **zero** fills where a configured registry's instrument
currency differed from the report currency: no existing test trades a foreign
instrument on a registry-configured pipeline.
`test_the_currency_blind_siblings_are_unchanged_in_this_release` is rewritten,
as its own docstring anticipated.

## Performance

Gate: ≤3% at 1,000 and 4,000 events against v2.11.0, back to back, identifier
draws identical, scaling no worse.

A first pass compared a fresh v2.12 run against a v2.11 number measured earlier
in the session and reported +0.39% at 1,000 events; repeating it minutes later
on identical code gave +3.18%. The swing is the machine, not the change — within
a single tree, run-to-run spread is 2.6% at 1,000 events and 5.1% at 4,000 —
so the figures below come from a properly interleaved A/B instead: one fresh
process per sample, trees alternating and reversing order each round, nine
rounds of five timed runs each, in a separate `v2.11.0` checkout whose module
provenance is asserted by path.

| | v2.11.0 | v2.12.0 | on medians | on minima |
| --- | --- | --- | --- | --- |
| 1,000 events | 321.8 ms | 324.1 ms | **+0.72%** | +1.01% |
| 4,000 events | 1369.3 ms | 1371.6 ms | **+0.17%** | +0.92% |

Both inside the gate, and both smaller than the noise floor of a single
measurement — which is the reason the gate says "back to back". At larger books,
where the guards are `O(positions)`: −0.24% at 200 positions and +0.46% at 1,000.

An optional single-pass `NAVCalculator` rewrite was measured and **deferred**:
the gate passes with margin, so it would be an optimisation outside the
release's theme carrying its own byte-identity risk.

An optional early settlement check inside `route_order` was also deferred, for a
reason found in the signature: it takes a `BrokerState`, a `BrokerProtocol`, an
`OMSOrder`, a timestamp, a mapping and a `RoutingConfig`, and none of them
carries the settlement currency. Giving it one means a new parameter on a public
broker-boundary function, for a check Seam 2 already makes and no caller can skip.

---

# [2.11.0] - 2026-09-07

**The Security Master.**

ADR-0016 N5 excluded `sector` from the canonical instrument key in v2.7, so that
classification could arrive later without re-identifying an instrument and
orphaning every fill recorded against it. Four releases later the field was
still populated by nothing — and, less obviously, still *writable* by nothing:
`register_instrument` refuses a record whose content differs from one it already
holds, and `InstrumentRecord.__eq__` does not ignore `sector`, so a field
documented as mutable raised on every attempt to change it.

Downstream of that, two consumers had been finished and left empty.
`AttributionMetrics.pnl_by_sector` has bucketed correctly since v2.6 — and
refused a `"UNCLASSIFIED"` placeholder — while receiving `sector_id=None` from
every pipeline fill ever produced, because `_trade_record` hardcoded it.
`ExposureStatus.sector_exposure` has been declared, typed, persisted and decoded
for longer than that and was never given a value at all. Meanwhile the registry
that could answer both was already threaded through the whole execution path on
`ExecutionPipelineConfig.instruments`, and was read from exactly one place:
`_classify_unpriced`, a route a healthy run never takes. The authority was
configured and consulted only when something had gone wrong.

## Added

- `alphalab.instrument.registry.classify_instrument(registry, asset_id, sector)`
  and `classify_instruments(registry, classifications)` — the write ADR-0016 N5
  kept the identity key clear for. Module-level functions rather than registry
  methods, because every write in that package is a function and the registry's
  own methods are pure reads. Keyed by `asset_id` rather than by an
  `InstrumentRecord`, which would admit one whose identity fields disagree with
  the registry's — a question with no good answer.
- `alphalab.instrument.record.normalize_sector_label(value, field_name="sector")`
  — strips surrounding whitespace and refuses an empty, whitespace-only,
  control-character or non-string label, while **permitting** internal
  whitespace and non-ASCII.
- **Sector reaches the run.** `ExecutionPipeline` resolves the classification
  once per fill in `_apply_report_to_portfolio` and freezes it onto that fill's
  `TradeRecord.sector_id`. `pnl_by_sector` produces a real breakdown for the
  first time.
- **Exposure by sector.** `ExposureStatus.sector_exposure` is filled from the
  same authority, on **signed** market value so the buckets sum to
  `net_exposure` over the classified positions, inside the pass that already
  walked them.

## Changed

- `_risk_exposure` is one pass rather than three. It previously built
  `asset_exposure` with a dict comprehension and then ran two `sum()` generators
  over the result; it now accumulates all four figures and the sector buckets in
  a single traversal. Every existing figure is unchanged, exactly — a
  no-registry run's serialized payload is byte-identical to v2.10.0's.
- `_sync_risk_from_portfolio` and `_risk_exposure` take the registry rather than
  defaulting it, so no call site can silently stop reporting. Both are private.
- `_trade_record` takes `sector` as an argument and stays a pure formatter with
  no access to pipeline state.
- `ExecutionPipelineConfig.instruments` is documented as read for two things
  rather than one. `record_for` remains the only method ever called on it: the
  pipeline still never resolves a provider symbol, derives an `asset_id`,
  registers anything, or classifies anything.

## Fixed

- **A field ADR-0016 called mutable could not be changed.** Reclassifying an
  instrument raised `InstrumentRegistrationError`. `classify_instrument` is a
  separate function rather than a relaxation of that refusal: relaxing it would
  make the rule depend on *which* field differs and leave accidental content
  drift indistinguishable from a deliberate reclassification. Re-registering a
  stale, unclassified record over a classified one is still refused.
- Three documentation defects found during v2.11 archaeology, all pre-existing:
  `ExecutionPipelineState.unpriced_assets` said "Not persisted; nothing captures
  this state" while `alphalab.runtime.snapshot` has captured and restored it
  since v2.9; the v2.9 changelog gave `apply_terminal_outcome` a signature it
  never had; and `test_no_session_or_run_snapshot_exists_yet` was written
  mid-v2.9 and had been vacuously true since the end of that same release. The
  test keeps its assertions, which are still worth pinning, and is renamed for
  what it actually checks.

## Unchanged, deliberately

`alphalab.analytics.attribution` — not opened. The consumer was already correct:
it buckets by `sector_id` when present, omits the trade when it is `None`, and
refuses a placeholder bucket. Touching a working consumer to make a supplier's
change look larger is the unnecessary refactor this release refuses.

Instrument identity derivation, `canonical_instrument_key`,
`ALPHALAB_INSTRUMENT_NAMESPACE`, `INSTRUMENT_KEY_SCHEME`, alias storage and
resolution, `register_instrument`'s refusal, `InstrumentRecord`'s constructor,
`RiskLimits` and every risk check — no check reads a sector, because
`sector_exposure` is visibility and not enforcement.

**No schema constant moves.** `PIPELINE_SNAPSHOT_SCHEMA` stays 2,
`SESSION_`, `BACKTEST_`, `ALLOCATION_`, `OMS_` and `LIFECYCLE_SNAPSHOT_SCHEMA`
stay 1, `PORTFOLIO_SNAPSHOT_SCHEMA` stays 2. `TradeRecord.sector_id` and
`ExposureStatus.sector_exposure` are existing persisted fields with existing
decoders; their values change and their shape does not.

**No exception class is added.** `InstrumentRegistrationError` means a
registration would replace one identity with another, and classification
provably cannot: the signature cannot express it, and `dataclasses.replace`
refuses `asset_id` outright because that field is `init=False`.

**No live trading is added.** No broker transport, no streaming source, no venue
connectivity.

## Deferred, unchanged

Any taxonomy or classification dataset — AlphaLab ships no reference data, so a
breakdown requires an operator who declares one; classification dimensions other
than sector; a canonical or case-folded sector vocabulary; distinguishing *why*
a sector is unknown, in the manner of `UnpricedReason`; sector-based risk limits;
persisting the `InstrumentRegistry`, or checking on `restore` that a supplied
registry classifies as the captured run did; `InstrumentRecord.currency` reaching
`Position.currency`, and the FX rate source and multi-currency valuation that
depend on it; `StrategyContext.history` and `.universe`; broker transport,
streaming market data and reconnect; artifact storage; the approval workflow over
`alphalab.enterprise`'s RBAC and audit log; and the removal of `integrations`,
`kernel`, `core.events` or `CommonEvent`.

## Verification

2926 tests pass, up from 2818. Strict mypy is clean over 896 source files, Ruff
lint and format are clean, and the wheel and sdist build and validate.

**Identity stability is pinned three ways**, because that is the guarantee
ADR-0016 N5 exists to protect: `sector` is outside the key, the classification
signature exposes no identity field, and `dataclasses.replace(record,
asset_id=...)` raises. ADR-0016's golden identifier
`2b670078-27a6-57c2-b359-4e64d8809ea2` is re-derived from a *classified* record.

**A run with `instruments=None` is byte-identical to v2.10.0** — measured, not
asserted. The v2.10.0 tree was extracted and run beside the candidate on one
machine over a seeded four-fill workload: the serialized pipeline payload matches
byte for byte, the identifier stream reaches the same 97 draws, and every
exposure figure is equal.

**The performance gate for `sector_exposure` passed on every measure.**
Interleaved against v2.10.0, 14 samples per cell: 1k best −0.47%, 1k median
−0.28%, 4k best −0.74%, 4k median −0.68%, scaling 4.124 → 4.113. All inside the
3% tolerance and all in the faster direction, because the single-pass rewrite
removed two traversals the old implementation made. The classified path itself
costs nothing measurable against a registry-configured-but-unclassified control:
+0.07% at 1k, −0.21% at 4k.

Simulated and venue fills agree on the sector **structurally**: both reach
`_apply_report_to_portfolio`, which is the only place the registry is read for a
fill, and a test drives `apply_execution_report` to prove it. Backtest, replay
and paper agree on every sector.

Reclassification is `O(1)` and shares structure: 200 successive classifications
of one instrument leave the `PersistentMap` store object identical and the
registry the same size.

## Architecture decisions

**ADR-0027** settles instrument classification and the sector provenance
boundary: the classification API and why it is keyed by `asset_id`, the sector
label rule and why it is not `normalize_key_field`, reclassification semantics,
the two-tenses provenance model, the single fill-path integration point, and the
compatibility position. Written before the implementation; its status line now
records what shipped. The one addition made during implementation is decision 3's
residual paragraph, which states that the label rule binds the classification
path and not `InstrumentRecord`'s constructor.

**ADR-0016** gains a status amendment naming exactly which two of its statements
this supersedes — testing invariant 9's second clause, conditionally, and the
non-goal on sector values, for the mechanism only — and which one it relies on:
N5's exclusion of `sector` from the identity key, unchanged.

---

# [2.10.0] - 2026-09-07

**The Strategy Boundary.**

v2.9 shipped a durable-continuation guarantee with four preconditions. Three
were the caller's and could be met. The fourth — "strategy-internal state
restored by the caller" — could be met by nobody, because `StrategyProtocol`
declared ten hooks and no way to express it: a strategy holding a rolling
window, a counter or a fitted model held state that was captured by nothing,
restored by nothing and, worst of all, **detected by nothing**. Restore
succeeded, every Class-1 value compared equal, and the run diverged on the next
event with nothing raising. The opposite direction was as empty:
`StrategyContext` had existed since v0.10.0 with nine fields, and every
construction site in the repository passed `object()` for six of them, so a
strategy could observe the event handed to its hook and nothing else — not its
position, not its cash, not its own orders, not the price it was about to be
marked at. This release closes both.

## Added

- `alphalab.strategy.protocol.StrategyStateProtocol` -- a **second, separate**
  protocol a strategy satisfies to declare durable internal state, with three
  members: `strategy_state_version`, `capture_state` and `restore_state`.
  Separate so that `StrategyProtocol` is unchanged and every existing strategy
  keeps compiling and keeps its current, conditional guarantee. Declaration is
  structural and opt-in; nothing introspects attributes to guess at state, and
  `BaseStrategy` deliberately provides no defaults, because inheriting a no-op
  `capture_state` would make every strategy claim state it does not have.
- **A required two-sided codec.** Both directions or neither: a strategy
  defining an encode and no decode is refused at capture rather than read as
  declaring nothing. The reason is measurable rather than stylistic -- the
  shared encoder writes `Decimal("1.25")` and `"1.25"` identically, and a
  `tuple` as a `list`, so no generic decoder can recover the type afterwards.
  "Restrict strategy state to values the encoder supports" specifies the write
  direction, which already worked, and leaves the read direction unspecified,
  which is the one that was broken.
- `StrategyStateRecord`, `NOT_ASKED` and `READABLE_PIPELINE_SCHEMAS` in
  `alphalab.runtime.snapshot`, and a **three-valued** `StrategyRecord.state`:
  the field absent means a version-1 payload whose writer never asked, `null`
  means the strategy declared none, and an object means it declared. `{}` and
  `null` never collapse.
- `alphalab.runtime.context_views` -- `PortfolioView`, `OrderView`,
  `OrderShare`, `RiskView`, `MarketView` and `order_shares_by_strategy`. Every
  view is a reference to state the pipeline already holds; nothing is copied and
  no view owns a fact.
- **A strategy sees the marked portfolio.** `ExecutionPipeline` assembles the
  context from the marked-portfolio and resynced-risk locals that already
  existed two lines above the dispatch that could not see them, never from the
  pre-mark `state.portfolio` -- reading those would show a strategy a book
  marked at the previous event's prices while risk evaluated its order against
  these. Risk headroom and a market view ship alongside, both being reference
  assembly over state already on the pipeline.
- **Strategy-scoped live order shares.** A strategy sees its *share* of an
  order, never sole ownership: two strategies whose intents net into one
  `BUY 100` see 60 and 40, and `OrderShare.is_sole_contributor` is `False` for
  both. The view covers live orders only, because ADR-0021 retires a
  contribution when its order reaches a terminal state.

## Changed

- `PIPELINE_SNAPSHOT_SCHEMA` moves **1 -> 2**, for one field per strategy
  record. `SESSION_SNAPSHOT_SCHEMA`, `BACKTEST_SNAPSHOT_SCHEMA`,
  `ALLOCATION_SNAPSHOT_SCHEMA`, `OMS_SNAPSHOT_SCHEMA`,
  `PORTFOLIO_SNAPSHOT_SCHEMA` and `LIFECYCLE_SNAPSHOT_SCHEMA` do **not** move.
  That is ADR-0023's envelope split working as designed, exercised for the first
  time: the run envelopes nest the pipeline payload and its decoder validates its
  own version.
- **Version-1 pipeline payloads stay readable**, and restore every strategy as
  "not asked". That is the OMS precedent rather than the portfolio's: a v1
  payload is missing nothing, and "no strategy state was captured" is an
  accurate reading of it rather than an invented value. One bounded exception --
  a *declaring* instance against a v1 payload is refused, because starting it
  empty would invent the one fact that decides whether the continuation is
  correct.
- **Capture validates and normalizes strategy state at capture time.** The value
  goes through the existing `DeterministicEncoder` immediately, so an
  unencodable state can never reach a `PipelineSnapshot` that looks valid in
  memory and fails at an unrelated `serialize` later. The same step leaves the
  record carrying JSON-decoded primitives, so `restore_state` receives one shape
  whether the snapshot travelled through JSON or not -- without it the in-memory
  path handed back `Decimal` and `tuple` while the JSON path handed back `str`
  and `list`, and a codec written against one would break on the other.
- `StrategyEngine.process_event` builds a context **only for a running
  strategy**. It previously built one for every registered strategy and
  `Dispatcher` discarded it a moment later, which was free while a context held
  placeholders. Dispatch semantics are unchanged and `Dispatcher` keeps its own
  guard.
- `ExecutionPipeline.process_market_event` overlays the pipeline-owned context
  fields onto the caller's factory result. `ContextFactory` keeps its signature,
  every existing construction site keeps working, and `clock`, `logger` and
  `config` pass through as the caller's own objects -- which is what keeps
  `alphalab.reinforcement_learning`'s `_PendingDecision` channel working
  untouched. A pipeline-owned field always wins, so no caller can install a
  second source of truth.

## Fixed

- Four reconciliation mismatches now refuse the **whole** restore, naming the
  strategy, with the original exception chained: a declaring instance against a
  version-1 payload, a declaring instance against a recorded `null`, a
  non-declaring instance against a payload carrying state, and a strategy's own
  decode raising. Nothing partial is returned; restoring the other strategies and
  skipping one would produce a state that is internally consistent, compares
  equal on every Class-1 value, and is wrong.

## Unchanged, deliberately

`StrategyProtocol` (ten hooks, none added, none revived), `StrategyState`,
`StrategyContext`'s field names, arity and types, `ContextFactory`'s signature,
`DeterministicEncoder` (no branch was added for strategy state; a strategy type
with no JSON form uses `__serializable__`, which already existed),
`PersistenceProtocol`, `SessionState`, `BacktestState`, `PersistentMap`, and
`alphalab.strategy` as a strict leaf importing only `alphalab.common` and
itself.

`StrategyRecord.config` keeps its v2.9 semantics exactly -- still `Any`, still
`config=require(payload, "config")`. Its JSON round-trip lossiness is
pre-existing, is a property of the shared encoder and of JSON rather than of
anything decided here, and ADR-0025 decision 4 records that a separate decision
is required before it changes.

**No live trading is added.** No broker transport, no streaming source, no venue
connectivity. Nothing in this release reaches a market.

## Deferred, unchanged

`StrategyContext.history` and `.universe`; allocation visibility through the
context; mutable runtime services in any context field; reviving `on_fill`,
`on_order` or `on_timer`, which would require a second strategy dispatch per
event and change intent ordering and every parity baseline; unifying
`SessionState` and `BacktestState`; promoting the four cross-package private
decoders; broker transport, streaming market data and reconnect; artifact
storage; a security master and sector attribution; an FX rate source and true
multi-currency valuation; the approval workflow over `alphalab.enterprise`'s
RBAC and audit log; and the removal of `integrations`, `kernel`, `core.events`
or `CommonEvent`.

## Verification

2818 tests pass. Independent certification -- probes written outside the
repository rather than reruns of the shipped suite -- exercised a
counter/deque/`Decimal`/nested-mapping/`__serializable__` strategy through
capture, JSON, restore into a **fresh** instance and continuation at every
record boundary of single- and multi-strategy datasets, and found Class-1 state,
declared strategy state, identifier sequences and behaviour identical to the
uninterrupted control in every case. Capture, restore and context assembly draw
**zero** identifiers, dispatch no strategy and mutate no state. Cross-engine
parity was measured rather than inferred: `BacktestEngine.run` and
`TradingSession(mode=BACKTEST)` agree on Class-1 state, strategy state,
processed count and identifier position, backtest equals replay equals paper,
and live differs only in routing.

The negative case is retained rather than removed: a stateful strategy that
declines to declare state **still diverges** across a fresh restore, and a test
names that expectation. That is the documented behaviour of a non-declaring
strategy, not a defect.

Attribution is `O(1)` when the contribution ledger is empty -- flat across 100,
500 and 2,000 historical orders -- and linear in live contributions at roughly
2.3 microseconds each. Context assembly is `O(1)` with respect to portfolio and
order-book size, and strategy-state capture scales with the size of the declared
state rather than with run length.

Pipeline throughput is materially equivalent to v2.9.0. Measured back to back on
one machine, v2.9.0 itself runs at roughly 3,466-3,479 events/sec with
1k -> 4k scaling of about 4.58-4.64x under current conditions, and this release
measures within run-to-run noise of that. The `<= 4.5x` scaling figure quoted
during v2.10 planning was taken when the machine was in a faster state and is
**not** met by the v2.9.0 release either; it is recorded here as a threshold that
needs recalibrating rather than as a target this release achieved.

## Architecture decisions

Two ADRs are written to disk, both drafted before their implementation and both
now recording what shipped. **ADR-0025** settles strategy-state ownership and
the capture contract -- the two-sided codec, the per-strategy version
independent of `PIPELINE_SNAPSHOT_SCHEMA`, the version-1 compatibility rule, the
failure taxonomy, and the explicit exclusion of `StrategyRecord.config`.
**ADR-0026** settles `StrategyContext` population and the visibility boundary --
what is populated and what stays empty, contribution-based order attribution,
the read-only boundary, and the factory overlay. ADR-0026's *Data model changes*
records the one place the implementation departed from its first draft: the
supporting marker protocols stay intentionally minimal rather than gaining the
views' methods, because typed members would make `alphalab.strategy` depend on
four domain packages, and the concrete views live outside that package instead.

---

# [2.9.0] - 2026-09-07

**Durable Run State.**

A run could not stop and continue. Every *quantity* already survived a round
trip -- cash, realized P&L, commission, positions, reservations, contributions,
`notional_allocated`, risk NAV, open orders -- but nothing recorded where the
identifier stream had reached, so a run that stopped and resumed re-entered
`id_scope`, built a fresh source positioned at zero, and drew identifiers it had
already used. Nothing raised at any layer. Around that defect sat four more:
nothing captured the composite state the execution path threads, the OMS payload
declared no schema version, a venue fill delivered twice was applied twice, an
externally routed order a venue had already ended could not be ended here, and
appending to `alphalab.persistence` was quadratic. This release closes all of
them.

## Added

- `alphalab.runtime.snapshot` -- `capture` / `restore` / `from_primitives` for
  `ExecutionPipelineState` under `PIPELINE_SNAPSHOT_SCHEMA = 1`. This is the
  state every environment threads: a backtest, a replay, a paper run and a live
  session all advance the same value through the same step, and until now
  nothing captured it. Eleven of its fifteen fields already serialized as they
  stood; the obstacle was never the state model but that four values are not
  data and no contract said what to do about them. The envelope nests the
  portfolio, OMS and allocation snapshots rather than decoding their contents a
  second time, and carries market, risk, execution, analytics and the strategy
  runtime inline under its own version.
- `alphalab.runtime.session_snapshot` (`SESSION_SNAPSHOT_SCHEMA = 1`) and
  `alphalab.backtesting.snapshot` (`BACKTEST_SNAPSHOT_SCHEMA = 1`) -- the run
  bookkeeping around that core, one envelope per owning package. Two rather than
  one because the dependency runs one way: `alphalab.backtesting` imports
  `alphalab.runtime` and never the reverse, so a single shared module would close
  an import cycle. Each nests the pipeline snapshot, and each carries the
  pipeline configuration exactly once, because `state.config.pipeline` and
  `state.pipeline.config` are the same object and two copies could disagree.
- `alphalab.allocation.snapshot` -- `ALLOCATION_SNAPSHOT_SCHEMA = 1` for the two
  ledgers that decide whether a restored run's committed capital and its
  attribution are correct. First-class rather than inline because ADR-0021 made
  their lifetime exact and ADR-0024 gave a restored working order a way to end,
  and neither guarantee survives a round trip the ledgers do not.
- `TradingSession.resume(state)` and `BacktestEngine.resume(state)` -- context
  managers that open the identifier scope a restored stream position implies.
  `restore` reconstructs state and does nothing else; `resume` is where
  continuation begins. The boundary is *between* `advance` calls: a run that
  processed N records resumes at record N+1 and replays nothing.
- `alphalab.common.ids.IdStreamPosition` -- `(seed, draws)`, the fact the repository
  never held. `DeterministicIdSource` counts what it mints, `current_id_position`
  reads the ambient cursor, `ExecutionPipelineState.id_position` stores it at each
  step boundary, and `id_source_for` rebuilds a source that has reached it. The
  position is two integers rather than a serialized generator state: it is
  readable, cross-checkable against the state it accompanies, and pins no
  generator implementation into the persisted format. Restore is therefore
  O(draws) -- roughly a microsecond per replayed draw, so a run that minted a
  million identifiers resumes in about a second, paid once.
- `ExecutionPipeline.apply_terminal_outcome(state, order, outcome, timestamp, reason="")`
  -- the route home for an externally routed working order the venue has ended
  as `CANCELLED`, `REJECTED` or `EXPIRED`. The caller supplies the outcome
  because the venue is the authority for what happened and the pipeline never
  infers it. `OMSEngine` moves the order and emits its event and
  `_release_if_terminal` retires both ledgers, exactly as they do for every other
  terminal transition. **No venue is contacted, no `BrokerState` is built, and
  `broker.reconciliation` is not consulted.**
- `OMS_SNAPSHOT_SCHEMA = 1` and `LEGACY_UNVERSIONED_V0_KEYS`
  (`alphalab.oms.snapshot`). `OMSSnapshot` was the last round-trip snapshot
  without a version field.
- `RuntimeObjects`, `SessionObjects` and `BacktestObjects` -- the live objects a
  snapshot records by type and requires the caller to supply back: the sizing
  model, the execution simulator, the optional instrument registry, each strategy
  instance, and the fill policy.

## Fixed

- **A continued run re-minted identifiers it had already used.** A seed says
  where a stream starts; nothing said where it had reached. Measured on v2.8.0,
  sweeping every restore point across a workload producing 41 identifiers found
  up to **4 duplicates** -- a later `fill_id` landing on a UUID an earlier
  `execution_id` already held -- against **zero** for the uninterrupted control
  over the same workload, so the restart caused them and not the workload.
  Nothing raised: two distinct facts in one run came to share an identifier and
  every layer accepted it. The same sweep on v2.9 over a 16-record seeded
  workload split at all 15 boundaries produces 257 identifiers and no duplicate,
  identical position for position to a run that never stopped.
- **A venue fill delivered twice was applied twice.**
  `ExecutionPipeline.apply_execution_report` is the seam a real venue arrives
  through, and `_apply_reports` never wrote to `ExecutionState`. The simulated
  path recorded a report because `ExecutionEngine.simulate` runs first; the venue
  path recorded nothing, so the `reports` map -- keyed by execution id, which is
  exactly the ledger a duplicate check reads -- stayed empty on the one path
  where redelivery happens. Measured on v2.8.0 for a partial fill: cash
  999,600 -> 999,200, position 4 -> 8, `filled_quantity` 4 -> 8, two pipeline
  fills for one venue execution. A *full* fill was caught only incidentally, by
  the OMS refusing to fill an order already `FILLED`. A repeated `execution_id`
  is now a no-op returning the state unchanged -- the rule
  `alphalab.broker.reconciliation` already stated for `DUPLICATE`, "a no-op
  rather than an error because a reconnect makes it routine", enforced over the
  pipeline's own ledger without importing the broker.
- **A working external order could not be ended.** Under `EXTERNAL` routing an
  accepted order is left working for a broker adapter and its reservation and
  contribution stay held, correctly. A venue fill had a route home through
  `apply_execution_report`; a rejection, cancellation or expiry had none, so the
  pipeline's whole public surface was seven methods and not one of them
  terminated an order. Measured on v2.8.0, six externally routed events left six
  open orders holding six reservations, six contributions and 3,000 of committed
  notional with no way to retire any of it. ADR-0021 recorded this as a known
  boundary; it is now closed.
- **Appending to `alphalab.persistence` was quadratic.**
  `MemoryStorage.append_event` rebuilt three containers per call -- the stored-event
  tuple, the `frozenset` of event ids and the system-event tuple -- and
  `save_snapshot` rebuilt the snapshot dict per save. Measured on v2.8.0, 32,000
  appends took **14.0 s** and each doubling cost ~4.5x the previous, so
  `benchmarks/benchmark_persistence.py`'s 100,000-event workload did not finish.
  The four fields now use `AppendOnlyLog`, `PersistentMap` and `PersistentSet` --
  the same containers v2.1 gave the engine histories and v2.2 gave the OMS order
  book, applied to the one package that missed them. The same 32,000 appends take
  **0.24 s**, the worst doubling is **2.02x**, and the 100,000-event benchmark
  completes in 1.97 s. `PersistenceProtocol` and every method signature are
  unchanged.
- **A restored state could hold a configuration `initialize` would have
  refused.** `_require_one_account_currency` -- v2.8's guarantee that
  `ExecutionPipelineConfig.currency` and `Account.base_currency` agree -- is
  called at exactly one site, inside `ExecutionPipeline.initialize`, and restore
  does not go through `initialize`. `restore` now re-runs every construction-time
  validation `initialize` enforces before returning a state, raising the same
  error with the same message. The rule is general, so a validation added to
  `initialize` later is covered.

## Changed

- **`OMSState` payloads now carry `schema_version`.** A stored payload of any
  vintage previously decoded as current, so the first schema change would have
  been a silent misread rather than a decision. `capture` never emits an
  unversioned payload. An unversioned payload is read **only** when its top-level
  key set is exactly `{orders, active_orders, completed_orders, history,
  events}`: the legacy shape with a key missing is refused, with an extra key is
  refused, any other unversioned shape is refused, and `schema_version >= 2` is
  refused. **A missing `schema_version` is never read as version 1.** It is sent
  to a total structural match a payload either passes whole or fails.
  Compatibility rather than a break because, unlike the portfolio's refused
  version 1, a pre-v2.9 OMS payload is missing no data at all -- every field the
  decoder reads is present -- and `alphalab.oms`'s module docstring teaches the
  JSON round trip as a public recipe, so those payloads exist by invitation.
- The regression guard on whole-state OMS serialization asserts a six-key
  payload where it asserted five. It is the same exact-set equality, not a
  loosened assertion.
- Execution-path throughput is unchanged within measurement noise: -0.38% at
  1,000 events and -1.15% at 4,000, against a 3% release tolerance. The OMS,
  replay and portfolio benchmarks are flat.

## Unchanged, deliberately

No breaking changes. `PersistenceProtocol`, `PORTFOLIO_SNAPSHOT_SCHEMA` (2) and
`LIFECYCLE_SNAPSHOT_SCHEMA` (1) do not move, and there is no migration
framework. `new_id`, `use_id_source`, `id_scope`, `derive_asset_id`, the frozen
instrument namespace and **every `new_id()` call site** are untouched: the
`ContextVar` was always a delivery mechanism and the defect was a missing state
field. `BrokerState` and `ExternalOrderMap` are not persisted -- a belief about a
venue is not durable state, and reconstructing one from a snapshot would assert
something AlphaLab cannot verify.

**This release does not add live trading.** No broker transport, credential or
venue call is introduced; `apply_terminal_outcome` is told what happened and
never asks.

**The equivalence contract is conditional and says so.** For a *seeded* run,
given the same records in the same order, the same supplied runtime objects, and
strategy-internal state restored by the caller, capture -> serialize ->
deserialize -> restore -> continue equals uninterrupted execution across
ADR-0023's Class-1 state and every deterministic identifier. `StrategyProtocol`
declares ten hooks and **no state-serialization hook**, so a strategy holding a
rolling window in Python attributes holds state AlphaLab cannot capture; the
fourth precondition is the caller's and a test names that expectation rather
than leaving it implicit. An **unseeded** run restores and continues with
quantities guaranteed and identifiers regenerated: `uuid4` cannot match a prior
run and cannot collide, and this carve-out is explicit rather than implied.

## Deferred, unchanged

`StrategyStateProtocol` and `StrategyContext` completion; unifying `SessionState`
and `BacktestState` or removing the parallel runtime mechanism; promoting the
four cross-package private decoders (`_order`, `_report`, `_fill`,
`_require_object`) to a public or shared surface; schema constants for market,
risk, execution, analytics or strategy state; a migration framework or
version-translation layer; broker transport, streaming market data and reconnect;
artifact storage; a security master and sector attribution; an FX rate source and
true multi-currency valuation; the approval workflow over `alphalab.enterprise`'s
RBAC and audit log; unifying `OMSState` and `BrokerState` authority; and the
removal of `integrations`, `kernel`, `core.events` or `CommonEvent`.

## Architecture decisions

Six ADRs are written to disk. **ADR-0019**, **ADR-0020** and **ADR-0021** record
decisions taken and shipped during v2.8 that were never written down; their
substance is not reopened. **ADR-0022** (deterministic identifier continuation),
**ADR-0023** (the run-state snapshot envelope) and **ADR-0024** (external order
recovery without a venue) are this release's decisions, and their status lines
now record the shipped implementation. ADR-0023's decision 1 records that the
run layer shipped as two envelopes rather than the single `RunSnapshot` it first
sketched, and why.

---

# [2.8.0] - 2026-09-06

**Currency Roles and Run Outcomes.**

Three fields stood for several things at once, and nothing noticed when they
disagreed. One configuration named two account currencies and silently switched
off two risk limits. One valuation added two currencies together and labelled
the result with one of them. One request could end without a fill and leave no
reason behind, and its allocation ledger entry behind forever.

## Added

- `ExecutionPipelineState.unpriced_assets` — the assets a run declined to trade
  for want of a price, keyed by `asset_id` so the size follows the distinct
  instruments a run's strategies named rather than the event count. Surfaced as
  read-only `unpriced_assets` properties on `BacktestResult`, `ReplayResult` and
  `SessionState`, which read through to the pipeline state so none can disagree
  with the run it describes. Not persisted.
- `UnpricedAsset` and `UnpricedReason` (`alphalab.runtime`, re-exported from
  `alphalab.backtesting`). `UnpricedAsset` keeps the reason, a sentence of
  detail, the first and last market timestamp and an occurrence count.
  `UnpricedReason` has exactly three members and no fourth for "unknown".
- `ExecutionPipelineConfig.instruments: InstrumentRegistry | None` — optional,
  read-only, and used for one thing: deciding whether a dropped request's
  `asset_id` names a registered instrument. Only `record_for` is ever called.
  The pipeline never resolves a provider symbol, never derives an `asset_id`
  and never registers anything; ADR-0016 leaves resolution at the wire boundary
  and this takes none of it back. `None` remains the default.
- `MixedCurrencyValuationError` (`alphalab.portfolio`), and
  `PortfolioValuation.assert_single_currency`.

## Fixed

- **A configuration could name two account currencies and say nothing.**
  `ExecutionPipelineConfig.currency` funds the cash ledger and denominates every
  `OrderInstruction`, `ExecutionReport` and `Position`; `Account.base_currency`
  is what the risk resync and `NAVCalculator` read. Nothing checked that they
  agreed. When they did not, cash landed under one and risk read the other, so
  `risk.cash`, `buying_power`, `current_nav` and `peak_nav` were all zero:
  `check_buying_power` refused every order, `check_leverage` returned early on a
  non-positive NAV, and `check_margin` passed vacuously. Measured with
  `currency="EUR"` against `base_currency="USD"` over six quotes — zero fills,
  six risk rejections, full starting equity reported, and two risk limits
  quietly not checking. Refused at the first statement of
  `ExecutionPipeline.initialize`, before the portfolio exists, so nothing is
  funded against a configuration that is about to be refused.
- **A valuation could span two currencies and report one number.**
  `PortfolioValuation.snapshot` read cash for the base currency alone, dropping
  every other balance, while `long_value` and `short_value` summed every
  position regardless of what it traded in. A book of 1000 USD and 500 EUR cash
  against 1100 USD and 1100 EUR of positions returned `equity=3200.00` labelled
  `"USD"` — a figure in no currency at all. Refused instead, on two conditions
  because there were two independent errors: every position must declare the
  base currency, and no other currency may hold a non-zero balance.
- **An allocation contribution could outlive its request.**
  `AllocationState.contributions` is retired by `_release_if_terminal`, which
  was reached only from inside the per-report loop — so any request or order
  ending without a report kept its entry forever. Four paths did: a request
  dropped for want of a price and one refused by risk never reach the OMS at
  all, while `NO_FILL` / `REJECTED` / `EXPIRED` and a withdrawn partial-fill
  remainder reach a terminal OMS state without producing a report. Twenty events
  on each path left twenty entries apiece, with no bound and no reader.
  Contributions are now retired wherever a request's lifecycle ends, exactly
  once, using the mechanism that already existed. Reservation release is
  unchanged on every path, and post-trade attribution still reads contributions
  at fill time.
- **A run could produce no fills and not say why.** Of the ways a request can
  end without a fill, every one but a dropped request left a reason: allocation
  and risk record their rejections, an externally routed order stays open in the
  OMS, and a non-trading execution closes its order with the status that ended
  it. A dropped request emitted only an `AllocationReservationReleased`, which
  is the identical event three other outcomes emit, and the reason lived on the
  per-event result and was gone when the run finished. This is the ADR-0016
  section 3 failure mode — a strategy naming an instrument the run never priced
  — which ADR-0016 documented and deferred.

## Changed

- `LIFECYCLE_SNAPSHOT_SCHEMA` is the literal `1` rather than an alias of
  `DEFAULT_SCHEMA_VERSION`, which is also the version of `CommonEvent` and
  `BaseEvent`. **The value does not move**, so no payload reads or writes
  differently; the lifecycle version is simply now independently settable. This
  is the trap v2.6 removed from `PortfolioSnapshot` and left standing here, and
  the prerequisite ADR-0018 names for the bump the governance release needs.

## Unchanged, deliberately

No breaking changes. No persisted type gains or loses a field, no schema version
moves, and there is no migration. `evidence_id_for` is byte-identical, so
evidence recorded under v2.6 and v2.7 still verifies and still passes its
policy. `canonical_instrument_key`, the frozen instrument namespace and
`asset_id` derivation are untouched, as are `Fill` / `Trade` validation,
`ExecutionPipelineResult.unpriced_requests`, `BacktestStep`, the OMS snapshot
format and every canonical market and execution model. No new dependency.

`PortfolioValuation.portfolio_value`, `long_value`, `short_value` and
`NAVCalculator.calculate` share the currency-blindness and are left exactly as
they were, with their present behaviour pinned by a test. `NAVCalculator` runs
on every market event through the risk resync, so guarding it is a hot-path
decision that belongs with the release supplying an FX rate source.

**This release does not add FX.** Refusing to aggregate two currencies is the
absence of a rate, not a rule that foreign-currency instruments are invalid: the
cash ledger is already keyed by currency and every position already declares its
own, so a wholly-EUR book values in EUR exactly as it did.

## Deferred, unchanged

FX and true multi-currency valuation; instrument currency reaching `Position` /
`CashLedger`; `Bar.currency`; `OMSSnapshot.schema_version` and the OMS
history/events envelope; `AllocationState` persistence and session round-trip;
governance actors and enterprise RBAC; richer persisted evidence provenance;
`ResearchState` dataset identity; `StrategyContext` population; live transport,
streaming and reconnect; sector classification; a dataset registry; an artifact
store; and the removal of `integrations`, `kernel`, `core.events` or
`CommonEvent`.

The design decisions behind this release -- that listing exchange, market-data
attribution and execution venue are three concepts and not one; that
`ExecutionPipelineConfig.currency` was standing for account, trade and
settlement currency at once; and that an unpriced request is a recorded outcome
rather than an error -- are not yet written up as ADRs.

---

# [2.7.0] - 2026-09-06

**Instrument Identity and Dataset Provenance.**

Two things that were asserted are now derived. A provider symbol becomes a
canonical instrument identity through an authority instead of being passed
through verbatim, and a run's evidence names the data the run actually consumed
instead of the data a caller claimed. Both were defects the repository
documented about itself.

## Added

- `alphalab.instrument` — the authority for what a provider symbol means.
  `InstrumentRegistry` owns `(provider, symbol) -> asset_id` and
  `asset_id -> InstrumentRecord`. A canonical `asset_id` is *derived*, not
  minted: `uuid5` over a fixed canonical key, under the frozen namespace
  `1935bdfa-e8c0-5611-ae10-607c3a67c19b`. Two independently configured
  environments therefore agree on the identity of one instrument with no shared
  database. Registration is still required — derivation alone would turn every
  typo into a new instrument. Registering the same record twice is a no-op;
  registering different content under an identifier the registry already holds,
  or pointing one provider symbol at a second instrument, is refused.
- `NormalizationPolicy.identity` — an explicit two-mode identity resolution,
  either an `InstrumentRegistry` or a named `UnresolvedIdentity`. It is never
  `None`: an absent value silently selecting the unsafe behaviour is the shape
  of the defect this release removes.
- `InstrumentResolutionError` (`alphalab.market.exceptions`) — raised at the
  wire boundary, naming the provider and the symbol.
- `BacktestResult.dataset_id` and `SessionState.source_id` — a finished run
  names the data it consumed. Both default to `None`, which is an honest
  absence rather than an invented identity.
- `ReplayResult.dataset_id` — reads through to the run it wraps, so a replay
  cannot disagree with its own backtest.

## Fixed

- **The documented provider path could not reach a fill (F-1).**
  `market.normalization` turned a provider symbol into an `asset_id` verbatim;
  every stage from `Quote` to `ExecutionReport` carried `asset_id` as an
  unconstrained `str`; and `core.Fill` refused anything that was not a UUID. The
  identity was wrong from the first record and nothing objected until the last,
  so market data, the strategy, allocation and risk all succeeded and the run
  died at the execution -> core adapter naming neither the provider nor the
  symbol. The only configuration that reached a fill was one where an operator
  hand-authored a UUID per instrument. Refusal now happens where the identity is
  created, and a registered instrument reaches a fill through the real path.
- **Evidence claimed a dataset instead of recording one.**
  `evidence_from_backtest` took a `dataset_id` argument, and `evidence_id_for`
  hashes that value — so the digest was only as trustworthy as a string somebody
  typed, and two runs over genuinely different data could be handed one identity,
  verify cleanly and pass the gate. `BACKTEST` evidence now derives the dataset
  from the run, and a run that names none is refused rather than recorded with a
  fabricated `""`.

## Changed — breaking

- **B1.** `NormalizationPolicy.symbols` is removed. A `SymbolMap` now lives on
  the identity mode: `NormalizationPolicy(symbols=SymbolMap({...}))` becomes
  `NormalizationPolicy(identity=UnresolvedIdentity(SymbolMap({...})))`. Two
  fields answering "what instrument is this symbol?" would be two sources of
  truth for one question.
- **B2.** `ProviderHistorySource.of` no longer defaults its `policy` argument.
  A defaulted parameter whose default value is always refused is a trap.
- **B3.** `evidence_from_backtest(result, subject, produced_at)` — the
  `dataset_id` parameter is removed, not accepted and ignored. An argument that
  is silently discarded leaves a caller believing they set something.
- **B4.** `DEFAULT_POLICY` is not a production execution configuration. Its
  identity mode is `UnresolvedIdentity`, which yields provider symbols, and
  `ProviderHistorySource.of` refuses it before calling the provider.

## Unchanged, deliberately

- `core.Fill` and `core.Trade` UUID validation. This release supplies a producer
  that can satisfy the existing invariant; it does not relax it.
- `evidence_id_for`, `ValidationEvidence`, `build_evidence`,
  `verify_evidence_id` and `evidence_from_research` are byte-identical to
  v2.6.0. Because the digest did not move, evidence recorded under v2.6 still
  verifies and still passes the policy it was promoted under — so this release
  needs no schema bump, no migration and no dual verification.
- `LIFECYCLE_SNAPSHOT_SCHEMA` remains 1 and `PORTFOLIO_SNAPSHOT_SCHEMA` remains
  2. No persisted type gained a field.
- `Intent` is unchanged and unvalidated, and `alphalab.strategy` acquires no
  dependency on `alphalab.instrument` or `alphalab.market`. The consistency
  guarantee is conditional on a strategy resolving through the canonical API;
  nothing intercepts an `Intent` that does not.

## Not in this release

Carried forward and explicitly **not** delivered: sector classification and a
security-master classification source (`InstrumentRecord.sector` is declared and
left `None`, and `pnl_by_sector` is still empty on the execution path);
governance actors and enterprise RBAC enforcement (ADR-0018 is written and
deferred); richer persisted provenance on evidence — source, time coverage,
normalization policy, provider identity; a dataset registry or uniqueness
guarantee; an artifact or object store; per-environment promotion policy;
code/version identity on runs; multi-currency valuation; live venue transport;
and the removal of `alphalab.integrations`, `alphalab.kernel` or
`alphalab.core.events`.

## Architecture decisions

- **ADR-0016** — Instrument Identity and Resolution Authority (Accepted).
- **ADR-0017** — Dataset Identity and Evidence Derivation (Accepted).
- **ADR-0018** — Governance Actors Across the Lifecycle / Enterprise Boundary
  (Proposed, deferred from v2.7).

---

# [2.6.0] - 2026-09-06

## Overview

AlphaLab 2.6.0 is "Allocation Authority and Attribution Truth". It is a
correctness release: no new engine packages, one new module
(`alphalab.core.contribution`), and two production-path numbers that were
plausible and wrong are now true.

Capital committed to orders that have not settled did not count against the
budget. Under `SIMULATED` routing that was invisible, because a reservation is
consumed or released inside the event that created it. Under `EXTERNAL` routing
— the routing ADR-0012 introduced for live — an accepted order stays working
and keeps its reservation by design, and six market events committed **5,400,000
against a 1,000,000 budget** with no position held and not one rejection
recorded.

Strategy identity was destroyed one statement before netting, and every emitted
order was stamped with the constant `"ALLOC-NETTED"` — including an order from a
single intent with no netting at all. Every strategy-level number the repository
could produce was a statement about a strategy that does not exist.

A third defect was found while verifying the first and is fixed with it: a
reservation is denominated at the reference price while consumption is
denominated at the execution price, so any fill priced away from the reference
stranded a residual on a terminal order, without bound.

See `docs/ADR/0015-allocation-authority-and-attribution-truth.md`.

## Added

- **`alphalab.core.contribution`** — `StrategyContribution(strategy_id,
  quantity)`, carrying a strategy's signed pre-netting share of a netted order,
  plus `contributions_from` for canonical aggregation. In `core` rather than
  `allocation` because it is a field of `OrderRequest` (ADR-0008); re-exported
  from `alphalab.allocation`.
- **`OrderRequest.contributions`** and **`TradeRecord.contributions`**, ordered
  by `strategy_id` so intent arrival order cannot change a request's value.
- **`AllocationState.contributions`** — a per-order ledger parallel to
  `reservations`, retired at the terminal OMS transition.
- **`AllocationEngine.contributions_for` / `retire_contributions`**.
- **`analytics.split_realized_pnl`** — signed weight `q_i / net_q`, with the
  last share obtained by subtraction so the parts sum exactly.
- **`Position.opened_at`** — when the current exposure began.
- Deprecation notices for `alphalab.integrations`, `alphalab.kernel` and
  `alphalab.common.CommonEvent`, all removed in v3.0.

## Fixed

- **Outstanding capital is enforced.** `AllocationEngine.allocate` compares
  `notional_allocated + total_notional` against both `available_global_capital`
  and `maximum_exposure`. Rejection stays whole-batch and atomic: `history`,
  `reservations` and `notional_allocated` are byte-identical after a refusal.
- **A terminal order releases whatever it still holds.** Released at the
  terminal OMS transition through the existing membership-guarded helper, so it
  is idempotent against every release point that already existed and covers both
  routings — including a venue fill arriving through `apply_execution_report`
  with no slippage model configured. Thirty round trips at a 1% adverse price
  previously stranded 3,000 against a 1,000,000 budget in a run ending flat.
- **`avg_holding_period` was a mean of zeros.** Holding periods are now measured
  from `Position.opened_at` for fills that reduced or closed a position.

## Changed — breaking

- **`TradeRecord` shape.** `strategy_id: str` is replaced by `contributions`;
  `sector_id` becomes `str | None`; `holding_period_seconds` becomes
  `float | None`. Positional construction breaks. No alias is provided: an alias
  would keep returning the wrong answer under a familiar name.
- **`PORTFOLIO_SNAPSHOT_SCHEMA` is 2, and version 1 payloads are refused.** No
  migration framework — a v1 payload does not record when a position opened, and
  `last_updated` would report a holding period of roughly zero for a position
  held a year. `DEFAULT_SCHEMA_VERSION`, `LIFECYCLE_SNAPSHOT_SCHEMA`,
  `CommonEvent.schema_version` and `BaseEvent.schema_version` remain **1**; the
  portfolio constant no longer aliases the shared one.
- **`OrderRequest.strategy_id` is `""` for every allocation-produced request.**
  `"ALLOC-NETTED"` is deleted. `oms.Order.strategy_id` and
  `ExecutionReport.strategy_id` carry the same empty value, and an order
  declaring no strategy is not entered in the OMS strategy index — so
  `orders_for_strategy` no longer answers for a fiction. The index stays
  single-valued and the query is neither renamed nor deprecated: it remains
  correct for callers who supply a real strategy id.
- **`pnl_by_sector` is empty for pipeline-produced reports.** There is no
  security master; a caller who has sector data still gets a breakdown.
- **`EXTERNAL` routing can now refuse an allocation** that would over-commit.
  Backtest, replay and paper are behaviourally unchanged.
- `IntentAllocator.size_intents` returns `(strategy_id, instrument, quantity)`
  triples; `NettingEngine.net_quantities` takes them.

## Deprecated

| Surface | Mechanism | Removed |
| --- | --- | --- |
| `alphalab.integrations` | `DeprecationWarning` at import | v3.0 |
| `alphalab.kernel` | `DeprecationWarning` at import | v3.0 |
| `alphalab.common.CommonEvent` | `DeprecationWarning` on use (PEP 562) | v3.0 |
| `alphalab.core.events` | documentation and ADR only — see below | v3.0 |
| `alphalab.allocation.BudgetExceededError` | documented as never raised | v3.0 |

`alphalab.core.events` deliberately emits **no** runtime warning.
`alphalab.core` re-exports thirteen of its symbols eagerly, so an import-time
warning there would fire on the canonical core package — which the whole
execution path depends on — for every consumer on every run.

## Documentation

Seven verified contradictions corrected (D-1…D-7): ADR-0014's claim that every
snapshot envelope carries `schema_version` (`OMSSnapshot` does not);
`ARCHITECTURE.md`'s stale partial-fill limitation, which the same document
contradicted; the allocation ledger table; `total_notional_allocated`'s
"historically" (it is outstanding, and falls); `OrderRequest.strategy_id`'s
claim that the sentinel applied only to cross-strategy netting; `TradeRecord`'s
"round-trip trade" (one record is produced per fill); and `BudgetExceededError`,
exported and raised nowhere.

## Not in scope

Unchanged and explicitly excluded: real broker transport, streaming market data,
an async live runtime, reconnect scheduling, order-state polling, multi-currency
valuation, artifact storage, Enterprise RBAC enforcement, dataset provenance, a
security master, a universal runtime, lifecycle → execution integration, and the
removal of `integrations`, `kernel`, `core.events` or `CommonEvent`. The
duplicate `ExecutionReceived` in `alphalab.broker` and `alphalab.brokers` is
**not** collapsed: they declare the same four fields in opposite order and are
both constructed positionally, so collapsing them would silently swap price and
quantity at one call site. Recorded in ADR-0015 for v3.0.

## Quality

| Gate | Result |
| --- | --- |
| `pytest -q` | **2122 passed** (2008 at v2.5.0) — 1698 unit, 109 integration, 315 regression |
| `mypy .` (strict) | clean, 924 source files |
| `ruff check .` / `ruff format --check .` | clean |

---

# [2.5.0] - 2026-09-05

## Overview

AlphaLab 2.5.0 is "State Round-Trip and the Live Data Path". It takes three
capabilities that already existed, were already tested, and were unreachable,
and makes them reachable — plus it decides two behaviours that had never been
decided.

Every AlphaLab state has serialized deterministically since v2.1. Exactly one
could be read back. v2.3 built a market-data normalization boundary and an
adapter protocol, and nothing in the repository joined them. The replay cursor
carried an O(N²) on a path v2.2 had wired into execution, and the benchmark
that nominally covered replay was written to avoid measuring it.

No new engine packages. Two new modules (`alphalab.market.provider`,
`alphalab.persistence.decode`), two new snapshot modules
(`alphalab.portfolio.snapshot`, `alphalab.lifecycle.snapshot`).

See `docs/ADR/0014-state-round-trip-and-the-live-data-path.md`.

---

## Added

### Typed state round-trip — `capture` / `restore`

- `alphalab.portfolio.snapshot` and `alphalab.lifecycle.snapshot` join
  `alphalab.oms.snapshot`: `capture(state)` → serializable projection,
  `from_primitives(payload)` → typed snapshot, `restore(snapshot)` → typed state.
- The contract, applied identically to every state: `restore(capture(s)) == s`.
  The restored value **compares equal**; it does not reproduce internal container
  lineage, and nothing can observe the difference. That is the position the
  repository already held — `oms.snapshot.restore` rebuilds the order book's
  indices by replaying `add`, and its test asserts equality against a state whose
  lineage is entirely different.
- Every snapshot carries `schema_version` and refuses one it does not read.
  There is no migration path in v2.5 because there is nothing to migrate from;
  the field exists so the first schema change is a decision rather than a silent
  misread.

### Typed decoding — `alphalab.persistence.decode`

- `require`, `as_decimal`, `as_optional_decimal`, `as_float`, `as_int`,
  `as_bool`, `as_str`, `as_optional_str`, `as_mapping`, `as_sequence`,
  `as_str_mapping`, `as_decimal_mapping`, `as_named_enum`, `as_value_enum`,
  `require_schema_version`. Each raises the new `StateDecodeError` **naming the
  field**.
- Deliberately *not* a reflective object mapper. A domain package states field by
  field what it expects, because that is where a wrong type or a missing key is
  caught. The failure this repository already had once — v2.1's append-only logs
  persisted as `"AppendOnlyLog([...])"` — came from a layer that accepted
  anything.
- Two enum encodings, two decoders, no guessing: a `StrEnum` is written as its
  value, a plain `Enum` through `str()` as `"Cls.NAME"`. A bare `"STAGING"` is
  refused, because accepting a form the encoder never writes is how a format
  acquires two dialects.
- `Decimal(str(value))`, the same conversion `market.normalization` uses, so a
  price between cents comes back as the number that was written.

### `PersistenceAdapter.snapshot_payload`

- The read direction, so a stored `Snapshot` can reach a domain decoder. It stops
  at primitives: the dependency runs domain → persistence and not back, or the
  package would have to import half of AlphaLab to read anything.
- `alphalab.persistence` now has production consumers for the first time. At
  v2.4 nothing in `alphalab/` imported it.

### The live data path — `alphalab.market.provider`

- `ProviderHistorySource` turns a provider adapter's historical bars into
  canonical `MarketRecord`s through `market.normalization`, and satisfies
  `MarketDataSource`. This is the link v2.3 was missing: before it,
  `normalize_wire_*` had no production caller and `SequenceSource` was the only
  source in the repository.
- `BarHistoryProvider` is deliberately narrower than any provider adapter's
  surface — `request_history` is the whole contract — so a test double can be a
  source without implementing connect, subscribe, quotes and books too.
- `normalize_wire_bars` maps a sequence; every normalization rule stays where
  v2.3 put it.
- A **history** source: finite, closed range, re-iterable, deterministic record
  ids on the `"<source_id>-<index>"` scheme `SequenceSource` and `MarketDataset`
  already use. No polling, no subscription, no reconnect, no streaming.
- Several symbols are interleaved by timestamp with an `asset_id` tie-break — a
  merge of already-sorted inputs, not a reordering. `validate_ordering` runs over
  the result either way.

### Ordering semantics — `SessionConfig.ordering`

- `CHRONOLOGICAL` (default): a record whose timestamp regresses **raises**. The
  source broke the guarantee it declared.
- `UNORDERED`: such a record is **skipped and recorded** on
  `SessionState.skipped`, the machinery the staleness gate already established,
  so an `UNORDERED` source cannot silently produce a run that merely looks
  chronological.
- A source declaring `UNORDERED` handed to a `CHRONOLOGICAL` session is refused
  by `TradingSession.run` before any record is processed — a session that would
  abort partway should not start.
- Nothing is buffered, reordered or held back. `MarketEngine.publish_quote`
  writes `latest_quotes` unconditionally and the pipeline marks the portfolio to
  whatever it finds, so a late record rewrites valuation backwards; AlphaLab says
  so rather than guessing.

### Benchmarks

- `benchmarks/benchmark_replay_engine.py` now measures `step_one_event` — the API
  the integrated replay path uses — across three sizes, and keeps the batch drain
  as a second figure rather than the only one.

---

## Changed

### A partially filled simulated order withdraws its remainder

The pipeline has always stated its rule in `_close_unfilled_order`: it mints a
fresh order per market event and never re-works an existing one, so an unfilled
order is withdrawn rather than left open. Every non-trading outcome was withdrawn
under that rule. A partial fill was the one branch that skipped it, so the order
stayed `PARTIALLY_FILLED` forever, holding the reservation for a quantity nothing
would execute. `LiquidityCappedFill` (v2.2) makes partial fills routine.

The remainder is now cancelled and its residual reservation released.
`Order.cancel` is legal from `PARTIALLY_FILLED` and preserves `filled_quantity`
and `average_fill_price`.

**This changes bookkeeping, not economics.** No fill is created or destroyed, so
cash, positions, realized and unrealized P&L, the equity curve, the accounting
identity and every analytics figure are exactly what they were. See
**Breaking changes**.

---

## Fixed

### The replay cursor was the last quadratic on a wired path

`ReplayState.system_events` was a tuple, and `step_one_event` appended one
`ReplayAdvanced` per record by rebuilding it. `ReplayBacktest` drives that method
once per record, so a replay of N records copied O(N²) elements — the same defect
v2.1 removed from the risk engine, v2.2 from the OMS and v2.3 from the market and
broker layers, left behind on the one path v2.2 had just wired into execution.

It survived three releases because the benchmark measured the *other* API. Its
own comment said it used the batch drain "to avoid astronomical tuple copying on
O(1M) elements" — steering around the defect rather than measuring it.

Measured, at N=2000/4000/8000 (linear is ~×2.00 per doubling):

| | N=2,000 | N=4,000 | N=8,000 | Growth |
| --- | --- | --- | --- | --- |
| v2.4.0 | 0.0165s | 0.0513s | 0.1740s | **×3.11, ×3.39** |
| v2.5.0 | 0.0089s | 0.0178s | 0.0355s | ×2.01, ×2.00 |

4.9× faster at N=8,000 and no longer growing; throughput flat at ~225,000
events/sec across all three sizes. Replay semantics are untouched: ordering,
contents, cursor behaviour and backtest/replay parity are unchanged.

### ADR-0012 said every vendor client was a stub

It was wrong about Binance, from v2.3 until now. A real HTTP transport
(`marketdata.transport.HttpTransport`, a genuine `urlopen`) and a real Binance
market-data client parsing `/api/v3/klines`, `/bookTicker`, `/trades` and
`/depth` have existed since **v1.39.0** (`5bfb6fa`), with twelve tests over
realistic payloads. It has never been run against a live endpoint from this
environment, so it is unverified — but it is not a stub.

It remains true that **no broker adapter reaches any venue**. The `integrations`
clients (Alpaca, IB, Zerodha) are canned-response stubs, and that is what
"AlphaLab does not support live trading" means.

### Documentation that disagreed with the code

- `docs/README.md` declared "Version v2.1.0" and "`ExecutionPipeline` is the only
  wired-together path" — three releases stale, and it did not mention
  `alphalab.lifecycle` at all.
- `docs/SYSTEM_DESIGN.md` listed `replay` as standalone and not invoked by
  `ExecutionPipeline`, which has been false since v2.2 (ADR-0010).

---

## Breaking changes

Confined to the simulated execution path's *bookkeeping*. No public name was
removed and no module disappeared.

### A partially filled order ends `CANCELLED`, not `PARTIALLY_FILLED`

| | v2.4.0 | v2.5.0 |
| --- | --- | --- |
| Final order status | `PARTIALLY_FILLED` | `CANCELLED` |
| `filled_quantity` / `average_fill_price` | preserved | preserved |
| `remaining_quantity` | positive, never worked | positive, recorded on a closed order |
| Membership | `active_orders` | `completed_orders` |
| Residual reservation | held indefinitely | released |
| Cash, positions, P&L, equity curve | — | **unchanged** |

Code asserting that such an order stays `PARTIALLY_FILLED` after the event that
filled it needs updating; five tests in this repository did.

A seeded run now mints one more identifier than before, because withdrawing
appends an `OrderCancelled` event. Quantities and money are unchanged, and
comparisons *between* two runs — backtest/replay parity, the deterministic
backtest regression — are unaffected.

### `SessionConfig` and `SessionState` gained fields

`SessionConfig.ordering` (defaults to `CHRONOLOGICAL`) and
`SessionState.last_record_timestamp` (defaults to `None`). Both are appended with
defaults, so positional construction is unaffected. A session fed records whose
timestamps go backwards now raises where it previously processed them and marked
the portfolio at a stale price.

### `ReplayState.system_events` is an `AppendOnlyLog`

It compares equal to the tuple it replaced, so `state.system_events == ()` still
holds and iteration is unchanged. A caller annotating the field type sees a
different one.

---

## Documentation

- `docs/ADR/0014-state-round-trip-and-the-live-data-path.md` — new; records the
  three design decisions and what was rejected.
- `docs/ADR/0012` — vendor-adapter table corrected, with the correction marked as
  such rather than quietly rewritten.
- `docs/ARCHITECTURE.md` — Implementation Status to v2.5; new sections on state
  round-trip, the live data path and partial-fill termination.
- `docs/README.md`, `docs/SYSTEM_DESIGN.md` — stale claims corrected.
- `README.md`, `ROADMAP.md` — v2.5 status and the remaining gaps.

---

## Quality gates

Measured on the release commit, not carried over:

| Gate | Result |
| --- | --- |
| `pytest -q` | **2008 passed** (1897 at v2.4.0) — 1698 unit, 109 integration, 201 regression |
| `mypy .` (strict) | clean, 915 source files |
| `ruff check .` | clean |
| `ruff format --check .` | clean, 959 files |
| `python -m build` + `twine check` | passing |

---

# [2.4.0] - 2026-09-05

## Overview

AlphaLab 2.4.0 is "Model + Strategy Lifecycle". Four packages each implemented
one stage of a lifecycle — `experiment_tracking` (v1.46.0), `model_registry`,
`research_assistant` and `deployment_manager` (all v2.0.0) — and none of them
met. Between them there was one link, `ModelVersion.run_id`, an unvalidated
string, and one convention: that a release component *might* say `"momentum@3"`,
which nothing produced and nothing parsed.

This release connects them, adds the three things that were missing at the
seams, and removes the quadratic behaviour all three stateful packages carried.

One new package, `alphalab.lifecycle`. It is an integration package, not a
fifth engine — the `alphalab.backtesting` pattern from v2.2. Each of the four
packages still works on its own and none of them changed shape to be composed.

**A deployment here is a lifecycle fact, not an operation on a machine.** It
records that an environment should be running a strategy version. It starts no
process, opens no connection and reaches no venue; AlphaLab still has no
transport to any real venue (ADR-0012 is unchanged). The registry references
artifacts and stores no bytes.

See `docs/ADR/0013-model-and-strategy-lifecycle.md`.

---

## Added

### The lifecycle package — `alphalab.lifecycle`

```text
research candidate → experiment run → validation evidence → model version
    → strategy version → promotion → deployment → rollback
```

- `LifecycleState` holds the four registries plus the evidence store, as one
  immutable, serializable value — the role `ExecutionPipelineState` plays for
  the execution path. Nothing is copied into it.
- Not to be confused with `alphalab.strategy.LifecycleState`, an enum naming
  the stages of a strategy *instance running inside a session*. A deployed
  strategy version is started and stopped many times without its stage
  changing.

### Strategy versions — `alphalab.lifecycle.strategy_version`

- `StrategyVersion` is the immutable, numbered record that did not exist.
  `studio.StrategyDefinition.version` was a free-form string, so nothing could
  be pointed at by a deployment or compared with the one before it.
- It carries the canonical `StrategyDefinition` rather than a second parameter
  format, so a candidate from `research_assistant.to_strategy_definition`
  reaches a strategy version with no shape in between.
- Four identities stay apart: the strategy line (`name`), the version, the
  `ModelRef` it runs, and the deployments it appears in. One version deployed
  to two environments is two deployments, which no field on the version could
  have said.
- `StrategyVersionRegistry` deliberately has **no** production index — see
  "one source of truth" below.

### Typed references — `alphalab.lifecycle.identity`

- `ModelRef`, `StrategyVersionRef`, `DeploymentRef`. A model version and a
  strategy version are both a name and a number, which is why they were passed
  as indistinguishable strings before; they are now different types that render
  the same way and do not compare equal.
- Rendering is `"name@version"` — the form `deployment_manager` already
  documented for release components. `parse_ref` reads it back, and a name
  containing `"@"` is refused at construction rather than producing a reference
  that parses to something else.

### Validation evidence — `alphalab.lifecycle.evidence`

- `ValidationEvidence` records what was measured, over what data, with what
  seed, and where the full report is. It computes nothing:
  `evidence_from_backtest` reads a run's `PerformanceReport` and
  `evidence_from_research` reads a `ResearchScore`. Both already existed and
  are referenced, not reimplemented.
- `evidence_id` is a SHA-256 digest of the evidence's own content — the
  construction `compute_checksum` already used for a release manifest. The same
  measurement identifies itself the same way without coordination, and
  `verify_evidence_id` detects numbers edited after the fact.
- `ValidationPolicy` states thresholds in advance. `evaluate_policy` names
  **every** failed check, not the first; a metric the policy asks for that the
  evidence does not carry is a failure, because an absent number is not a
  passing one; and evidence that no longer matches its own id fails before a
  single threshold is read.
- `required_method` lets a policy insist on `BACKTEST` evidence rather than
  numbers typed in by hand.
- **What a pass claims** is that the stated thresholds were met by the recorded
  numbers. Not statistical significance, not out-of-sample validity, and not a
  correction for how many candidates were searched first.

### The promotion gate — `alphalab.lifecycle.promotion`

- `promote_strategy_version` requires passing evidence, and requires the model
  version the strategy runs to be staged itself: a strategy cannot be more
  validated than the model inside it.
- It refuses to reach `PRODUCTION` at all. A strategy version goes live by
  being deployed, so the ledger is the only thing that ever puts one there.
- `retire_strategy_version` refuses to archive a version that is still active
  somewhere — taking down what is live is a rollback or a replacement, not a
  stage edit.
- Every accepted move appends a `StrategyPromotionRecord` carrying *why*, so a
  promoted version records what it passed rather than only that it passed.

### Checked references — `alphalab.lifecycle.registration`

- `register_model_version` and `register_strategy` verify that a cited
  experiment run exists and has **completed**, and that a cited model version is
  real. `ModelVersion.run_id` was an unvalidated `str | None`; neither package
  could check it alone without depending on the other.

### Deployment and rollback — `alphalab.lifecycle.deployment`

- `deploy_strategy_version` builds the release manifest from the version's typed
  references, makes it active in an environment, moves the version to
  `PRODUCTION`, and archives whichever version it displaced — unless that
  version is still active in another environment.
- One release package stands for one strategy version however many environments
  it reaches. A strategy version is immutable, so its manifest is fixed.
- `rollback_environment` restores the version the append-only ledger names as
  previously active, archives the one being taken down, and re-derives the
  model's deployment note. Deterministic: the same state and environment always
  roll back to the same place.
- Redeploying the version already running in an environment is refused, and
  nothing is registered before the refusal.

### Artifact references — `alphalab.model_registry.ArtifactRef`

- Where a version's trained bytes live, what they should hash to, and how big
  they are. AlphaLab never reads, writes or hashes those bytes: there is no
  object store here and this release does not pretend there is.
- `ModelVersion.__serializable__` projects a version to its metadata plus that
  reference, dropping the in-memory `model` object. An arbitrary object has no
  deterministic JSON form, and stringifying it would produce a payload that
  reads back as prose — the failure v2.1 removed from the append-only logs.

### Single-pass mapping views — `PersistentMap.items()` / `.values()`

`Mapping`'s default views iterate the keys and then index the map, resolving
every key twice. On a persistent map a resolution is a chain probe, not a hash
lookup, so the second one is real work. Iterating a 500-entry map's values is
1.7× faster, for every `PersistentMap` in the repository.

### The declared stage transitions — `alphalab.model_registry.stages`

- `LEGAL_TRANSITIONS` states every legal move once. `illegal_stage_move` is the
  pure table check, shared with `alphalab.lifecycle` so the table is not written
  twice.

### Benchmarks

- `benchmarks/benchmark_lifecycle.py` — three growth shapes (many versions of
  one line, many lines, a deep environment ledger) plus a full
  promote → deploy → rollback sweep.

---

## Changed

### One source of truth for what is live

`model_registry.DeploymentMetadata` was a hand-set blob claiming a version was
deployed somewhere; `deployment_manager`'s ledger recorded what actually was.
Both remain, but the integrated path now **derives** the note from the
deployment that happened rather than letting a caller assert one, and updates it
on rollback. `alphalab.lifecycle.views` answers "what is running here?" by
reading the ledger.

### `ParamValue` has one definition

`experiment_tracking.tracker.ParamValue` and `model_registry.registry.ParamValue`
were identical, and the registry's own docstring said consolidating them was
owed. Both now re-export `alphalab.common.types.ParamValue`. `MetadataValue` is
deliberately *not* the same alias: metadata admits `None`, a parameter does not.

---

## Fixed

### The lifecycle registries were quadratic

Every write copied the whole mapping or rebuilt the whole tuple, and three write
paths scanned the data they were writing to. Measured on one machine, per
doubling of the workload — linear is ~2x:

| Operation | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `log_metric` (2k → 4k → 8k) | 0.0091 → 0.0313 → 0.1226s (**3.4x, 3.9x**) | 0.0094 → 0.0179 → 0.0357s (1.9x, 2.0x) |
| `start_run` (2k → 4k → 8k) | 0.0165 → 0.0508 → 0.1738s (**3.1x, 3.4x**) | 0.0114 → 0.0196 → 0.0428s (1.7x, 2.2x) |
| `register_model`, one name (2k → 4k → 8k) | 0.0120 → 0.0442 → 0.1592s (**3.7x, 3.6x**) | 0.0073 → 0.0145 → 0.0310s (2.0x, 2.2x) |
| `register_model`, many names (2k → 4k → 8k) | 0.0113 → 0.0371 → 0.1341s (**3.3x, 3.6x**) | 0.0079 → 0.0190 → 0.0374s (2.4x, 2.0x) |
| `promote` (1k → 2k → 4k) | 0.0525 → 0.2000 → 0.7769s (**3.8x, 3.9x**) | 0.0126 → 0.0251 → 0.0543s (2.0x, 2.2x) |
| `register_release` (1k → 2k → 4k) | 0.0041 → 0.0124 → 0.0423s (**3.0x, 3.4x**) | 0.0044 → 0.0089 → 0.0181s (2.0x, 2.0x) |
| `deploy` (0.5k → 1k → 2k) | 0.0077 → 0.0261 → 0.0975s (**3.4x, 3.7x**) | 0.0056 → 0.0109 → 0.0222s (2.0x, 2.0x) |

The containers are the ones v2.2 introduced: `PersistentMap` where a key is
rewritten, `AppendOnlyLog` where a history only grows.

End to end on the benchmark suites, v2.3.0 → this release:

| Benchmark | v2.3.0 | v2.4.0 | Change |
| --- | --- | --- | --- |
| `benchmark_model_registry` rollback + re-promote (1k) | 3.45s / 290 per sec | 0.03s / 30,301 per sec | **104×** |
| `benchmark_deployment_manager` `active_release` (50k) | 2.99s / 16,727 per sec | 0.03s / 1,523,693 per sec | **91×** |
| `benchmark_model_registry` `production_version` (50k) | 1.78s / 28,038 per sec | 0.03s / 1,923,545 per sec | **69×** |
| `benchmark_model_registry` promote (2k over 20k versions) | 2.68s / 746 per sec | 0.04s / 50,456 per sec | **66×** |
| `benchmark_deployment_manager` rollback + re-deploy (1k) | 1.24s / 1,618 per sec | 0.03s / 73,482 per sec | **45×** |
| `benchmark_deployment_manager` deploy (5k) | 0.52s / 9,549 per sec | 0.03s / 154,437 per sec | **16×** |
| `benchmark_model_registry` `register_model` (20k) | 0.97s / 20,673 per sec | 0.08s / 248,404 per sec | **12×** |
| `benchmark_deployment_manager` `register_release` (10k) | 0.25s / 39,871 per sec | 0.04s / 226,184 per sec | **5.6×** |
| `benchmark_experiment_tracking` `log_metric` (10k) | 0.19s / 52,802 per sec | 0.04s / 262,332 per sec | **5.0×** |
| `benchmark_experiment_tracking` `best_run` (10k × 501 runs) | 0.56s / 17,736 per sec | 2.48s / 4,030 per sec | **0.23× — slower** |

### The one regression: readers that scan a whole map

`best_run` compares every run in a tracker on every call, and resolving a key in
a `PersistentMap` is a chain probe rather than a hash lookup. Scanning 501 runs
ten thousand times therefore costs about 4× what it did against plain dicts.
Both are O(runs) per call; only the constant changed.

That is the trade, and it is the right way round: the writers stopped being
quadratic and the readers stayed linear. It is the same trade v2.3 documented
for market-data ingestion at universe 1, one order of magnitude further along.

`ExperimentRun.metrics` in particular is a persistent map and not a plain dict
because a run has **two** growth axes: the values logged to a metric, and the
number of distinct metric names. A run logging a value per feature, per asset
or per layer has thousands of the latter. A draft of this release made `metrics`
a dict to buy back 25% of the `best_run` constant, and made logging distinct
metric names quadratic (3.8× per doubling) to do it. The regression test now
holds both axes.

`PersistentMap.items()` and `.values()` are new, and recover part of it.
`Mapping`'s default views resolve every key twice — once to decide it is present
and once to read it — which on a persistent map means two chain probes.
Iterating a 500-entry map's values is 1.7× faster as a result, for every reader
in the repository, not only these.

Three scans needed indexes, which no container change would have fixed:

- `promote()` called `production_version()`, which scanned every version of the
  model. `ModelRegistry` now carries `production` (name → current production
  version) and `production_line` (name → the versions that have held it, in
  order). Both are O(1).
- `rollback()` rebuilt the filtered promotion history to find the version to
  return to. It is now `production_line[-2]`.
- `deploy()` called `active_release()`, which scanned the whole ledger
  backwards. `DeploymentManager` now indexes the same records by environment.

`tests/regression/test_lifecycle_registry_complexity.py` guards both growth axes
— versions per name *and* number of names. An early draft of this release fixed
the first and made the second 50× slower and still quadratic, by inspecting
every entry of the container inside `__post_init__`, which runs on every write.

### A promotion could move a version anywhere

`promote()` refused only a move to `NONE` and a move to the stage the version
was already in. See **Breaking changes**.

---

## Breaking changes

Confined to `alphalab.model_registry` and `alphalab.experiment_tracking`. No
public name was removed, no module disappeared, and no enum member was removed.

### Two stage transitions are now refused

| Move | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `PRODUCTION → STAGING` | allowed | **refused** |
| `ARCHIVED → PRODUCTION`, version never in production | allowed | **refused** |
| `ARCHIVED → PRODUCTION`, version is the rollback target | allowed | allowed |
| `NONE → PRODUCTION` | allowed | allowed |
| `ARCHIVED → STAGING` | allowed | allowed |

A live version leaves production by being archived or replaced; a quiet
demotion leaves the model with nothing live and no record that anything was
taken down. An archived version that was never live returning to production is a
resurrection, not a restore — and if it were allowed, "roll back" would stop
being a distinguishable operation.

`NONE → PRODUCTION` stays legal at the registry level deliberately: the registry
is mechanism, and requiring evidence is policy, which lives in
`alphalab.lifecycle`.

### Container types on three state classes

Fields that were `dict` / `tuple` are now persistent containers. Both compare
equal to what they replaced — `run.metrics["loss"] == (0.5, 0.3)` and
`registry.promotions == ()` still hold — and `__post_init__` converts a
hand-built plain mapping, so runtime construction keeps working. A caller that
*annotates* one of these field types sees a different one.

| Field | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `ExperimentTracker.runs` | `Mapping[str, ExperimentRun]` | `PersistentMap[str, ExperimentRun]` |
| `ExperimentRun.metrics` | `Mapping[str, tuple[float, ...]]` | `PersistentMap[str, AppendOnlyLog[float]]` |
| `ModelRegistry.versions` | `Mapping[str, tuple[ModelVersion, ...]]` | `PersistentMap[str, PersistentMap[int, ModelVersion]]` |
| `ModelRegistry.promotions` | `tuple[PromotionRecord, ...]` | `AppendOnlyLog[PromotionRecord]` |
| `DeploymentManager.releases` | `Mapping[str, tuple[ReleasePackage, ...]]` | `PersistentMap[str, AppendOnlyLog[ReleasePackage]]` |
| `DeploymentManager.deployments` | `tuple[DeploymentRecord, ...]` | `AppendOnlyLog[DeploymentRecord]` |

**`ModelRegistry.versions[name]` changed indexing.** It was a positional tuple,
so `registry.versions["alpha"][0]` was version 1; it is now keyed by version
number, so that expression raises `KeyError` and `registry.versions["alpha"][1]`
is version 1. Use `list_versions(registry, name)` — the documented accessor,
whose return type is unchanged — or `get_version(registry, name, version)`,
which is now O(1).

### New fields on two state classes

`ModelRegistry` gained `production` and `production_line`; `DeploymentManager`
gained `environments`. They are indexes, maintained by their packages' own
functions the way `oms.book.OrderBook` maintains its own. Positional
construction of either class is unaffected (the new fields are appended and
default to empty), but a registry built by hand from `versions` alone will
report no production version until one is promoted — build through
`register_model` and `promote`.

---

## Documentation

- `docs/ADR/0013-model-and-strategy-lifecycle.md` — new.
- `docs/ARCHITECTURE.md` — Implementation Status updated to v2.4; the four
  lifecycle packages move out of the standalone list.
- `README.md`, `ROADMAP.md` — v2.4 status, capabilities and remaining gaps.
- `examples/12_model_lifecycle.py` — new: a research candidate through a real
  backtest to a deployment and back, including the refusal of a promotion that
  no evidence supports.

---

## Quality gates

Measured on the release commit, not carried over:

| Gate | Result |
| --- | --- |
| `pytest -q` | **1897 passed** (1756 at v2.3.0) |
| `mypy .` (strict) | clean, 905 source files |
| `ruff check .` | clean |
| `ruff format --check .` | clean, 948 files |

---

# [2.3.0] - 2026-09-05

## Overview

AlphaLab 2.3.0 is "Market Data + Broker/Live Execution". It is a connectivity
and convergence release: it establishes one canonical market-data model with an
explicit normalization boundary, one canonical broker adapter boundary, and
makes historical, replay, paper and live execution take the same canonical step
through the same engines.

It closes both items v2.2 deferred: market-data model convergence
(`data` / `market` / `marketdata` / `feed` / `live`, and the multiple `Bar`
types) and `broker` / `brokers` consolidation.

No new engine packages. Two new modules on the integrated path
(`alphalab.runtime.session`, `alphalab.runtime.broker_routing`) and three in the
market layer (`alphalab.market.record`, `.source`, `.normalization`).

**AlphaLab does not support live trading.** It supports the adapter contract a
live venue would be reached through; there is no connectivity to any real venue
in this repository. See `docs/ADR/0012` for the precise implemented /
adapter-only / absent breakdown.

See `docs/ADR/0011-canonical-market-data-model.md` and
`docs/ADR/0012-broker-boundary-and-environment-parity.md`.

---

## Added

### The market-data normalization boundary — `alphalab.market.normalization`

- `normalize_wire_quote` / `normalize_wire_trade` / `normalize_wire_bar` /
  `normalize_wire_book` lift `alphalab.data.feed` wire records into the
  canonical `alphalab.market` domain records the execution path consumes.
- Every number converts through `Decimal(str(value))`. `Decimal(0.1)` keeps the
  float's binary expansion; going through `str` keeps the number the provider
  wrote. That is what makes normalization deterministic.
- `NormalizationPolicy` supplies what the wire cannot carry (venue, currency,
  timeframe) and names an unattributed venue `"UNKNOWN"` rather than guessing.
  `SymbolMap` rewrites a provider symbol to an `asset_id`.
- Fields a wire record does not report — vwap, trade count, book order counts,
  trade direction — are documented as unreported, not invented.
- `is_stale` / `reject_stale` treat staleness as a caller decision, separate
  from validation: a stale record is well-formed, and how old is too old is a
  property of the strategy.

### The market-data adapter boundary — `alphalab.market.source`

- `MarketDataSource` yields canonical `MarketRecord`s and nothing else, so the
  execution path cannot tell a stored file from a socket.
- `SequenceSource` (finite, re-iterable, deterministic record ids),
  `OrderingGuarantee` (a source declares whether it can promise chronological
  order), `validate_ordering`.
- No provider API is modelled: no HTTP, no websockets, no vendor
  authentication, no reconnect loop.

### The canonical market record — `alphalab.market.record`

- `MarketInput` and `MarketRecord` moved here from
  `alphalab.backtesting.dataset` (re-exported unchanged), so a live feed
  adapter can produce a record without importing the backtesting package.
- `records_from_inputs` assigns deterministic, fixed-width record ids.

### Broker reconciliation — `alphalab.broker.reconciliation`

- `ExternalOrderMap` holds `oms_order_id ↔ broker_order_id` and refuses to
  rebind either direction.
- `classify_execution` is total: `APPLIED`, `DUPLICATE`, `UNKNOWN_ORDER`,
  `TERMINAL_ORDER`, `OVERFILL`, `INVALID`. Nothing is silently dropped.
- `apply_execution` is idempotent in `execution_id`; a refused fill leaves the
  state untouched.
- `ReconciliationLog` keeps every refusal, separating expected redelivery from
  a genuine break.
- `reconcile()` compares local state against a venue snapshot and produces
  `ReconciliationReport` — missing, unknown, divergent orders, divergent
  positions, cash difference. It states differences and does not resolve them.

### Trading sessions — `alphalab.runtime.session`

- `TradingSession` drives any `MarketDataSource` through
  `ExecutionPipeline.process_record`.
- `ExecutionMode` (`BACKTEST` / `REPLAY` / `PAPER` / `LIVE`) declares its own
  routing and whether its clock is moving.
- `max_market_data_age_seconds` gates stale records in a real-time session;
  skipped records are recorded with a reason rather than silently dropped.

### The venue boundary — `alphalab.runtime.broker_routing`

- `route_order` sends an accepted OMS order to a venue, with two pre-trade
  gates: never on a connection that is not `CONNECTED`, and never twice for one
  OMS order. The client order id is derived from the OMS order id, so a retry
  after a lost response addresses the same order.
- `apply_broker_execution` brings a venue fill back through
  `ExecutionPipeline.apply_execution_report` — the same function a simulated
  fill uses, so OMS, portfolio, allocation and analytics are identical either
  way.
- `routable()` projects an OMS order onto `broker.adapter.OMSOrderProtocol`,
  which the real `oms.order.Order` did not actually satisfy.

### The canonical step — `alphalab.runtime.execution_pipeline`

- `ExecutionPipeline.publish_record` and `process_record`. Every environment
  takes this step; `backtesting.engine.advance` delegates to it.
- `ExecutionPipeline.apply_execution_report` applies a report that did not come
  from the simulator.
- `ExecutionRouting` (`SIMULATED` / `EXTERNAL`) on `ExecutionPipelineConfig`.
  `EXTERNAL` leaves an accepted order working, invents no fill, and keeps its
  allocation reservation held.

### Benchmarks

- `benchmarks/benchmark_market_data.py` — normalization cost per record type
  and an ingestion sweep across universe size.

---

## Changed

### Market-data models converged

- `alphalab.marketdata.feed` re-exports `alphalab.data.feed`'s `Quote`, `Trade`,
  `Bar`, `OrderBookLevel` and `OrderBook`. They were field-for-field identical
  copies; they are now the same class objects.
- `alphalab.live.message.OrderBookLevel` is `alphalab.data.feed.OrderBookLevel`.
- `data.Bar` and `market.Bar` both remain, deliberately — different layers, not
  a duplicate. `tests/regression/test_market_model_convergence.py` asserts the
  distinction.

### Broker models converged

- `alphalab.brokers` routes the canonical types from `alphalab.broker`:
  `BrokerOrder`, `BrokerExecution` (`ExecutionReport`), `BrokerAccount`
  (`AccountSnapshot`), `BrokerPosition` (`PositionSnapshot`),
  `BrokerOrderStatus` (`OrderStatus`), and `AssetClass`, which was a fifth copy
  of `core.enums.AssetType`.
- `BrokerProtocol` covers order status, execution reception, account and
  positions. `PaperBroker` implements all of it.
- `ConnectionStatus` gains `RECONNECTING` and `FAILED`, which call for
  different behaviour: hold orders versus refuse them.
- `BrokerOrderStatus` gains `SUBMITTED` from the connector package, so
  broker-local operational states are one shared set.
- The routing events in `alphalab.brokers.events` name their identifier
  `broker_order_id` rather than `order_id`. `OrderManager` always passed the
  venue handle into that field, so only the name changes — but leaving it
  called `order_id` would have preserved, inside the converged package, exactly
  the ambiguity that decided which broker order model was canonical.

### Moved (all re-exported, no import breaks)

- `id_scope` / `id_source` → `alphalab.common.ids`, so a session can mint
  reproducible identifiers without importing the backtesting engine.
- `UnsupportedRecordError` → `alphalab.market.exceptions`. It is no longer a
  subclass of `BacktestError`; four environments publish records now.

---

## Fixed

### The broker layer was quadratic

`BrokerState` and `BrokerConnectorState` rebuilt their order, execution,
position and account indexes with `dict(old)` and grew `events` with
`(*events, e)` on every transition, so a session copied O(N²). v2.3 routes
paper and live execution through those states, which would have made this the
slowest part of a long session.

Measured, v2.2.0 → this release:

| Benchmark | v2.2.0 | v2.3.0 | Change |
| --- | --- | --- | --- |
| `benchmark_broker` (100k orders) | 676.70s / 148 per sec | 4.65s / 21,485 per sec | **145×** |
| `benchmark_live` (100k ticks) | 219.02s / 457 per sec | 1.28s / 78,338 per sec | **172×** |
| `benchmark_feed` (100k events) | 42.95s / 2,328 per sec | 0.69s / 144,489 per sec | **62×** |
| `benchmark_brokers` (10k cycles) | 2.44s / 4,106 per sec | 0.34s / 29,465 per sec | **7.2×** |
| `benchmark_marketdata` (100k trades) | did not run | 0.57s / 174,454 per sec | — |
| `benchmarks_market_engine` (quotes) | 105,670 per sec | 147,727 per sec | 1.4× |

`MarketState`, `MarketDataState`, `LiveState`, `FeedState` and
`MarketDataCache` moved to the same persistent containers.

### Market-data ingestion cost scaled with the universe

`MarketEngine.publish_*` rebuilt the whole `latest_*` index on every publish,
so a publish cost O(universe): 20k quotes into a 20,000-instrument universe ran
at 22,688 per sec against 215,808 per sec into a one-instrument universe, a
9.5× penalty that grew with the universe. Ingestion is now flat — ~195,000 per
sec at every universe size measured, 1 through 20,000.

The one regression is ~8% at universe 1, where a persistent map costs more than
copying a one-key dict. That is the trade, and it is the right way round.

### `benchmark_marketdata.py` could not run

It connected through `YahooAdapter`, whose client raises `NotImplementedError`
because it used to return hardcoded fake data. It fails identically on v2.2.0.
It now uses an explicit in-benchmark test double, which is what a benchmark of
the *engine* should have depended on. No vendor connectivity is faked.

### `oms.order.Order` did not satisfy `broker.adapter.OMSOrderProtocol`

Its `order_id` is an `OrderId`, not a string, and it has no single `price`. The
protocol claimed to decouple the broker layer from the OMS while not actually
matching it. `broker_routing.routable()` does the translation explicitly, at
the adapter boundary where it belongs.

---

## Breaking changes

Confined to `alphalab.brokers`, whose types are now the canonical ones. No
public name was removed from any package, no module disappeared, and no enum
member was removed — the breaks below are all changes of *shape*, not of
availability.

### Dataclass shapes

- `AccountSnapshot` takes `cash` / `equity` / `available_funds` instead of
  `cash_balance`, and requires the fields a venue account actually reports.
  `broker_id` and `metadata` are optional.
- `ExecutionReport` and `BrokerOrder` name their order field
  `broker_order_id`; `ExecutionReport.account_id` moved after `timestamp` and
  now defaults. `BrokerOrder` additionally requires `oms_order_id`, because a
  single `order_id` could not say whether it held AlphaLab's identifier or the
  venue's.
- **`PositionSnapshot` changed shape, not just field membership.** It was nine
  required fields (`position_id`, `account_id`, `symbol`, `asset_class`,
  `quantity`, `average_price`, `market_price`, `unrealized_pnl`,
  `realized_pnl`); it is now six required plus three defaulted:

  | | v2.2.0 | v2.3.0 |
  | --- | --- | --- |
  | `position_id` | required | **removed** — it restated the `"<account_id>:<symbol>"` key the state already stores the position under |
  | `market_value` | absent | **required (new)** |
  | `symbol`, `quantity`, `average_price`, `unrealized_pnl`, `realized_pnl` | required | required |
  | `account_id` | required | optional, defaults to `""` |
  | `asset_class` | required | optional, defaults to `AssetType.EQUITY` |
  | `market_price` | required | optional, defaults to `Decimal("0")` |

  Because the arity and the order both changed, **positional construction
  breaks**: a v2.2 call passing nine positional arguments will raise, and a
  call passing six will silently bind them to different fields. Construct with
  keywords. `market_price` and `market_value` are both kept because a venue
  reports both and they are different numbers — the mark for one unit, and the
  mark for the whole holding.

### Keyword-parameter renames — `order_id` → `broker_order_id`

Positional callers are unaffected; keyword callers break. Every affected public
entry point:

| Callable | v2.2.0 | v2.3.0 |
| --- | --- | --- |
| `BrokerConnectorEngine.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `OrderManager.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `brokers.list_executions` | `(state, order_id)` | `(state, broker_order_id)` |
| `brokers.validate_execution` | `(state, execution_id, order_id)` | `(state, execution_id, broker_order_id)` |
| `brokers.validate_order_cancellation` | `(state, order_id)` | `(state, broker_order_id)` |

The routing events in `alphalab.brokers.events` are renamed for the same
reason: `OrderSubmitted`, `OrderCancelled`, `OrderFilled` and
`ExecutionReceived` name their identifier `broker_order_id` instead of
`order_id`. The *value* was always the venue handle — `OrderManager` has always
passed `order.broker_order_id` — so only the name changes, and field order is
unchanged, leaving positional construction working. Nothing in the codebase
read the field by name.

### Enum values

Both enums are now aliases of canonical types, so their `.value` changed.
**`.name` is unchanged in every case**, and nothing in AlphaLab reads `.value`
on either — but external code that persisted or transmitted a `.value` will
read it back differently.

| Member | v2.2.0 `.value` | v2.3.0 `.value` | `.name` |
| --- | --- | --- | --- |
| `AssetClass.EQUITY` | `1` (`Enum`, `auto()`) | `"equity"` (`StrEnum`) | unchanged |
| `AssetClass.FUTURE` / `OPTION` / `FOREX` / `CRYPTO` | `2`–`5` | `"future"` / `"option"` / `"forex"` / `"crypto"` | unchanged |
| `OrderStatus.SUBMITTED` | `1` | `2` | unchanged |

`AssetClass` is now `alphalab.core.enums.AssetType` and therefore also gains a
`CASH` member; `OrderStatus` is now `alphalab.broker.order.BrokerOrderStatus`
and gains `PENDING_SUBMIT` (which takes value `1`, shifting `SUBMITTED` to `2`)
and `PENDING_CANCEL`. Both are widenings: no member was removed.

### Protocols and state containers

- `BrokerProtocol` (both packages) gained methods; a v2.2 adapter will not
  satisfy the v2.3 protocol.
- `MarketState`, `BrokerState`, `BrokerConnectorState`, `MarketDataState`,
  `LiveState` and `FeedState` fields are `PersistentMap` / `AppendOnlyLog`, not
  `dict` / `tuple`. Reads are unchanged (both are `Mapping` / `Sequence`);
  constructing one with a plain `dict` is a type error.

Every assertion in the existing tests is preserved. Only construction sites
moved.

---

## Quality

- 1737 tests pass (1599 at v2.2.0).
- `ruff check`, `ruff format --check`, `mypy --strict` clean.
- `python -m build` and `twine check dist/*` clean.
- All 11 examples run.
- 47 of 48 benchmarks run. `benchmark_workbench.py` fails on an unrelated
  workbench tab-lifecycle assertion and fails identically on v2.2.0; it is not
  in v2.3's scope.

---

# [2.2.0] - 2026-09-05

## Overview

AlphaLab 2.2.0 is "Unified Backtesting + Replay". It turns the existing research,
market-data, execution, portfolio and analytics components into one deterministic
dataset → analytics workflow, and closes the four v2.1 limitations that stood in
its way: the super-linear OMS order book, the leaked allocation reservation on a
risk rejection, the unserializable `OMSState`, and a replay engine that never
reached the execution path.

One new package, `alphalab.backtesting`. It is an *integration* package: it adds
no engine and no domain model, and composes `ExecutionPipeline` instead. There is
no backtest-only order model, no backtest-only fill model and — the point of the
release — no second set of portfolio books.

See `docs/ADR/0010-unified-backtesting-and-replay.md`.

---

## Added

### Unified backtesting — `alphalab.backtesting`

- `MarketDataset` / `MarketRecord` — an ordered, validated sequence of canonical
  market inputs (`Quote` / `Bar` / `Tick`), each carrying the `event_id` and
  `timestamp` `replay.HistoricalEventProtocol` requires. One dataset type feeds
  both drivers.
- `backtesting.engine.advance` — the canonical step: publish one record to the
  market engine, hand the resulting event to
  `ExecutionPipeline.process_market_event`.
- `BacktestEngine.run(config, dataset, strategy_state, context_factory)` — walks
  a dataset through that step and returns a `BacktestResult` with the final
  pipeline state, per-record `BacktestStep`s, the equity curve, the valuation and
  the compiled performance report.
- `BacktestConfig` — the execution-path config, the fill policy, the seed, and
  the analytics parameters, as one value.
- Read-only views: `final_equity`, `final_cash`, `realized_pnl`,
  `unrealized_pnl`, `commission_paid`, `equity_values`, `submitted_orders`,
  `executed_fills`, `steps_with_fills`, `performance_report`.

### Replay on the execution path — `alphalab.backtesting.replay`

- `ReplayBacktest.run(...)` drives `ReplayEngine`'s cursor and calls the *same*
  `advance` for every event it yields, so backtest/replay parity is structural
  rather than a coincidence the tests happen to observe.
- `alphalab.replay` itself is unchanged in responsibility: it still owns the
  cursor, the replay clock, the session lifecycle and chronological validation.
- The replay clock is the record index, not wall time, so a replay's own state is
  a pure function of its dataset.

### Execution semantics — `alphalab.execution.policy`

- `FillPolicy` decides one order's outcome at one market event from a
  `LiquidityContext` (asset, side, requested quantity, event price, size shown)
  and returns a `FillDecision`.
- `ImmediateFill` (default, fills in full), `StaticFill` (the pre-v2.2 fixed
  `fill_status` argument, as a policy) and `LiquidityCappedFill` (fills up to a
  share of the size the event showed; partial when capped, no fill when it showed
  none).
- `ExecutionPipeline.process_market_event` / `process_quote` gained an optional
  `fill_policy` parameter, which takes precedence over `fill_status` /
  `fill_quantity`. Both previous arguments still work unchanged.

### Persistent containers — `alphalab.common.persistent_map`

- `PersistentMap` and `PersistentSet`: immutable `Mapping` / `Set` with O(1)
  amortized update and structural sharing, using the same "shared append-only
  storage plus copy on branch" idiom `AppendOnlyLog` established in v2.1. Older
  versions keep observing exactly what they observed before; iteration is in
  first-insertion order.

### Deterministic identifiers — `alphalab.common.ids`

- `DeterministicIdSource(seed)` and `use_id_source(source)` scope where
  identifiers come from. `BacktestConfig.seed` installs one for a run and records
  it on the result.
- All identifier factories on the execution path now route through `new_id()`,
  `alphalab.core.ids.new_uuid` included.

### OMS snapshots — `alphalab.oms.snapshot`

- `capture` / `restore` / `from_primitives`, plus `OMSSnapshot` and
  `OMSEventRecord`.
- `alphalab.common.serialization` recognises a `__serializable__()` projection,
  which is how a type whose in-memory shape has no JSON form declares one.

### Benchmarks

- `benchmarks/benchmark_backtesting.py` — backtest and replay throughput, the
  scaling factor across a 4x workload, the replay cursor's overhead, and a parity
  assertion at both sizes.

---

## Fixed

### The OMS order book was quadratic

`OrderBook.add` rebuilt the whole order `dict` and both index `frozenset`s,
`OrderBook.replace` rebuilt the order `dict`, and `OMSEngine._update_sets`
rebuilt both order-id `frozenset`s — once per stored order, and the OMS stores an
order on submit and again on every lifecycle transition. Submitting N orders
copied O(N²) entries. All five containers are now persistent.

### The execution report index was quadratic too

Found by running the whole benchmark suite after the order-book fix, which is
the only reason it was found at all: `benchmarks_execution.py` took 85s for
100k fills. `ExecutionEngine.execute` and `partial_fill` stored a report by
rebuilding the whole `ExecutionState.reports` dict -- the same defect as the
order book, on the same execution path, paid by every fill a backtest produces.
`ExecutionState.reports` is now a `PersistentMap`; it is still an immutable
`Mapping` keyed by execution id and still serializes as the JSON object it
always did.

### Measured

On the development machine, full history retained:

| Benchmark | v2.1 | v2.2 |
| --- | --- | --- |
| `benchmark_oms` (100k order lifecycles) | 26.3 min | 6.7s |
| `benchmark_oms` scaling (10k → 20k) | 4.70x | 2.06x |
| `benchmarks_execution` (100k fills) | 85.3s | 1.55s |
| `benchmark_execution_pipeline` (4000 events) | 1.79s | 1.03s |
| `benchmark_execution_pipeline` scaling (4x workload) | ~7.4x | ~4.4x |

The residual above 4.00x in the pipeline benchmark is the cyclic garbage
collector walking a growing live heap, not an algorithmic term: with the
collector paused the same path scales 2.06x and 2.08x per doubling.

### A risk-rejected request leaked its allocation reservation

`AllocationEngine.allocate` commits capital against every request it emits.
A request that risk refused was skipped with a bare `continue`, and a request
with no market price was skipped earlier still; neither released anything, so
`notional_allocated` over-reported for the rest of the run.

`AllocationState.reservations` is now a per-order ledger. The allocation engine
owns the amount, the pipeline owns the moment, and releasing an order that holds
no live reservation raises `UnknownReservationError` instead of silently
subtracting — which is what makes "released exactly once" checkable.

### `OMSState` could not be serialized as a whole state

`OrderBook` keys orders by `OrderId`, a dataclass, which JSON cannot use as an
object key. Rather than weakening the identifier, the state now declares an
explicit projection: orders serialize as an array in submission order, the
derived indices are omitted and rebuilt on restore, and every event carries an
`event_type` tag so the log reads back as typed events.
`restore(from_primitives(deserialize(serialize(state)))) == state`, and the
restored state is a working state the engine carries on from. Values without such
a projection are still rejected by the encoder rather than stringified.

### Replay produced nothing

`alphalab.replay` sequenced events and never reached a strategy, an order or a
portfolio. It now drives the real path through `alphalab.backtesting.replay`.

---

## Changed

- **Breaking:** `AllocationEngine.release_reservation(state, order_id, timestamp)`
  no longer takes the amount to release — the ledger owns it.
- `OMSState.active_orders` / `completed_orders` are `PersistentSet[OrderId]`
  rather than `frozenset[OrderId]`. They compare equal to a `frozenset` in both
  directions and support `in`, `len` and iteration as before; iteration is now in
  insertion order rather than hash order.
- `OrderBook.orders()` and `orders_for_asset` / `orders_for_strategy` return
  orders in submission order.
- `AllocationState` gained `reservations`; `AllocationEngine` gained
  `reserved_notional`, and `alphalab.allocation` gained the `reserved_for_order`
  and `open_reservations` views.
- `benchmarks/benchmark_oms.py` reports a scaling factor and fails if it exceeds
  3.00x. It and `tests/regression/test_oms_book_complexity.py` pause the cyclic
  collector around their timed sections, because otherwise the growth ratio
  measures the collector rather than the data structure.
- `benchmark_execution_pipeline`'s scaling ceiling tightened from 12.0x to 6.0x.

---

## Tests

1599 tests pass (1429 on v2.1.0). New:

| Area | File |
| --- | --- |
| Persistent containers | `tests/unit/common/test_persistent_map.py` |
| Reservation ledger | `tests/unit/allocation/test_reservations.py` |
| Fill policies | `tests/unit/execution/test_fill_policy.py` |
| OMS snapshots | `tests/unit/oms/test_oms_snapshot.py` |
| Dataset validation | `tests/unit/backtesting/test_dataset.py` |
| Backtest loop | `tests/unit/backtesting/test_engine.py` |
| OMS complexity | `tests/regression/test_oms_book_complexity.py` |
| Execution report index complexity | `tests/regression/test_execution_reports_complexity.py` |
| Reservation leak | `tests/regression/test_risk_reservation_leak.py` |
| Whole-state OMS serialization | `tests/regression/test_oms_state_snapshot.py` |
| Run-to-run determinism | `tests/regression/test_deterministic_backtest.py` |
| Full backtest path | `tests/integration/test_backtest_pipeline.py` |
| Backtest/replay parity | `tests/integration/test_backtest_replay_parity.py` |

`tests/regression/test_state_serialization.py` no longer pins `OMSState` as
unserializable; it pins the fix, and that a raw dataclass-keyed mapping is still
rejected.

---

## Not in this release

Deferred to v2.3: market-data model convergence (`data` / `marketdata` / `feed`,
and the three separate `Bar` types), `broker` / `brokers` consolidation, and live
broker connectivity into the execution path. See `docs/ARCHITECTURE.md`,
"Known gaps and deferred areas".

---

# [2.1.0] - 2026-09-04

## Overview

AlphaLab 2.1.0 is "Execution + Portfolio Correctness". It makes the existing
execution spine correct and fast rather than adding new packages: mark-to-market,
a single monetary precision policy with an exact accounting identity, explicit
execution invariants, correct persistence of engine histories, and the fix for
the O(N^2) event accumulation that stopped the risk benchmark from completing.

No new packages. No new domain models. `PortfolioState` remains the single
canonical portfolio state and `oms.order.Order` the single lifecycle order.

---

## Breaking Changes

Public symbols or behaviour changed. Import sites and callers may need updating.

- **Engine histories are `AppendOnlyLog`, not `tuple`.** `RiskState`,
  `MarketState`, `ExecutionState`, `OMSState`, `AllocationState`,
  `PortfolioState`, `TransactionLedger` and the `ExecutionPipelineState`
  accumulators now hold `alphalab.common.AppendOnlyLog`. It is an immutable
  `Sequence` and compares equal to tuples and lists, so `len()`, indexing,
  slicing, iteration, `in`, `reversed()` and `== (...)` are unchanged. Code that
  required a literal `tuple` (`isinstance(..., tuple)`, concatenation with `+`)
  must call `.to_tuple()`.
- **`PortfolioEngine.apply_fill` rejects malformed fills** with
  `InvalidTransactionError`: non-positive price, negative commission, and a
  quantity that is zero or rounds to zero at `SHARE_QUANT`. These previously
  produced an incoherent position, a fabricated `PositionClosed` event, or a
  ledger entry for a trade that did not happen.
- **Every non-trading execution outcome is terminal for the order.** A
  rejected, expired or unfilled execution moves the OMS order to `REJECTED` /
  `EXPIRED` / `CANCELLED` and out of `active_orders`; previously it stayed
  `ACCEPTED` and open forever. `Order.reject` accordingly accepts `ACCEPTED` in
  addition to `NEW` / `PENDING`; an order that has already traded still cannot
  be rejected.
- **One portfolio snapshot per market event**, not one per fill.
  `ExecutionPipelineState.portfolio_snapshots` now also has a point for events
  that only marked the book and did not trade.
- **`DeterministicEncoder` no longer stringifies unknown objects.** `Decimal`,
  dataclasses, `AppendOnlyLog`, `Enum` and `UUID` are handled by explicit
  branch; anything else raises `SerializationError` naming the type. Callers
  that relied on the previous silent `str()` fallback were receiving payloads
  that could not be read back.
- **`AnalyticsEngine.compile_report`, `AllocationEngine.allocate` and
  `calculate_attribution`** accept `Sequence` where they previously required
  `tuple`. Existing tuple callers are unaffected.

---

## Added

- **Mark-to-market.** `PortfolioEngine.update_market_prices` is wired into
  `ExecutionPipeline.process_market_event` and runs *before* any decision is
  taken on the event, so unrealized P&L and NAV reflect the current market and
  the risk state is resynced from the marked book. It moves unrealized P&L only
  -- cash, realized P&L, commissions and the ledger are untouched. Non-positive
  prices are rejected as invalid market data and unheld assets ignored; a
  position with no price keeps its previous mark. Emits `MarketValueUpdated`
  when something was actually re-marked.

  The marked portfolio reaches **risk** only. The strategy's `StrategyContext`
  comes from the caller's `context_factory`, which the pipeline does not
  populate, and allocation sizes from market prices and its capital budget.
- **`PortfolioValuation.snapshot` / `PortfolioValuationSnapshot`** -- the
  deterministic read model over `PortfolioState`: cash, long/short/positions
  value, unrealized and realized P&L, commissions, and equity. Carried on
  `ExecutionPipelineResult.valuation` and projected into the analytics
  `PortfolioSnapshot`. Not a second portfolio state.
- **`PortfolioState.realized_pnl` and `PortfolioState.commission_paid`** --
  cumulative account totals that survive a position being closed and dropped
  from `positions`.
- **`alphalab.portfolio.money`** -- the portfolio's single monetary precision
  policy (see *Monetary precision* below), and **`Position.cost_basis`**, the
  authoritative money figure it rests on.
- **`ExecutionPipelineResult.unpriced_requests`** -- order requests dropped
  because the pipeline had no market price for the asset.
- **`alphalab.common.AppendOnlyLog`** -- immutable append-only sequence with
  O(1) amortized append and copy-on-branch structural sharing.
- **`benchmarks/benchmark_execution_pipeline.py`** -- end-to-end pipeline
  throughput and scaling benchmark.
- Tests: `tests/unit/common/test_append_log.py`,
  `tests/unit/portfolio/test_portfolio_invariants.py`,
  `tests/unit/portfolio/test_monetary_precision.py`,
  `tests/integration/test_mark_to_market_pipeline.py`,
  `tests/regression/test_event_accumulation_complexity.py`,
  `tests/regression/test_state_serialization.py`.

---

## Monetary precision

`alphalab.portfolio.money` holds one rounding policy for the whole portfolio:

1. **Money is exact at the currency minor unit.** Every monetary amount stored
   in `PortfolioState` -- cash, cost basis, realized P&L, commissions, market
   value -- is an exact multiple of `0.01`. `to_money` is the only place
   rounding happens.
2. **Rounding happens once, at entry.** `PortfolioEngine.apply_fill` rounds the
   fill's notional and commission as they enter, and both the cash movement and
   the position's cost basis are derived from those same rounded values.
3. **Prices and quantities are inputs, not money.** They keep their own finer
   precision (`PRICE_QUANT` 1e-4, `SHARE_QUANT` 1e-6).

`Position.cost_basis` is the authoritative money figure -- the exact cash paid
(long) or received (short) for the open quantity. Realized P&L is the difference
between money in and money out; unrealized P&L is `market_value - basis`.
Splits (partial close, reversal) round one part and derive the other by
subtraction, so the parts always sum to the exact whole. `average_cost` is
derived from the basis and keeps its meaning; a `Position` constructed without a
`cost_basis` derives one from `average_cost * |quantity|`.

The accounting identity is therefore **exact** -- an identity over exact Decimal
values for any price and quantity the engine accepts, not an approximation that
happens to hold for round numbers:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

---

## Fixed

- **O(N^2) event/history accumulation.** Every engine grew its append-only
  history with `(*state.events, event)`, rebuilding the whole tuple on each
  transition; N transitions copied O(N^2) elements. Measured on the development
  machine, with full history retained in every case:

  | Benchmark | v2.0.0 | v2.1.0 |
  | --- | --- | --- |
  | `benchmark_risk_engine` (100k evaluations) | 285.7s | **1.4s** |
  | `benchmarks_market_engine` (100k quotes) | 2,060 ops/sec | **~167,000 ops/sec** |
  | `benchmarks_market_engine` (100k books) | 640 ops/sec | **~165,000 ops/sec** |
  | `benchmark_portfolio_engine` (20k fills) | 1.90s | **0.22s** |
  | `benchmark_execution_pipeline` (4000 events) | 6.76s | **~1.8s** |

  `benchmark_risk_engine` was 9.5x outside its own 30s budget on v2.0.0.
  `benchmark_portfolio_engine`'s full 100k-fill workload could not complete on
  v2.0.0 at all; it now runs in ~1.9s. End-to-end, the pipeline benchmark's
  scaling across a 4x workload dropped from ~17x to ~7.5x.
- **The accounting identity was not exact.** The cash ledger rounded
  `quantity * price + commission` while the position independently rounded
  `(exit_price - average_cost) * quantity`. Two roundings of one economic event
  disagreed by up to half a cent each and the error accumulated: an ordinary
  penny-spread quote (bid 100.00 / ask 100.01, mid 100.005) put the identity out
  by $0.01, and randomized multi-asset portfolios drifted by up to $0.05. See
  *Monetary precision* above.
- **Append-only histories serialized as a repr string.** `dataclasses.asdict`
  recurses into tuples but deep-copies anything else, so an `AppendOnlyLog`
  reached `DeterministicEncoder`, whose `str()` fallback wrote
  `"events": "AppendOnlyLog([...])"` instead of a JSON array. It raised nothing
  and passed snapshot validation, so `PersistenceAdapter.to_snapshot` of a
  migrated state silently persisted unreadable history.
  `alphalab.common.dataclass_to_dict` now does its own recursion -- `asdict`'s
  behaviour plus one rule: an `AppendOnlyLog` converts like the tuple it
  replaced.
- **Realized P&L was discarded when a position closed.** Closing removes the
  position from `positions`, taking its `realized_pnl` with it, so account-level
  realized P&L was unrecoverable after a round trip. It now accumulates on
  `PortfolioState`.
- **Analytics trade records could be credited with another fill's P&L.**
  `ExecutionPipeline._trade_record` scanned the whole portfolio history in
  reverse for the asset's last realized-P&L event, so an opening fill inherited
  an earlier close's P&L and the performance report overstated realized P&L on
  every re-entry. It now reads only the events the current fill produced.
- **An order for an asset with no market price raised `KeyError`.** Allocation
  prices unknown assets at `0.00`; the pipeline now drops such requests before
  the OMS and reports them on `unpriced_requests`. The condition is per-event --
  a later quote makes the asset tradeable.
- **`FillStatus.NO_FILL` left the order open.** It now moves the order to
  `CANCELLED`, removes it from `active_orders`, and releases the allocation
  reservation exactly once -- fabricating no fill, trade or position.
- **Venue-rejected orders stayed open forever** in `oms.active_orders`, so open
  orders never reconciled with fills.
- **Positions were never repriced between fills**, so unrealized P&L, NAV, risk
  NAV and the equity curve were stale until the next trade.
- **`benchmarks/benchmarks_market_engine.py` could never run.** Its first quote
  carried timestamp `0.0`, which `market.timestamp.is_valid_timestamp` rejects
  as not strictly positive, so the benchmark raised `MarketValidationError`
  immediately. The sequence now starts at `1.0`. (Pre-existing on v2.0.0.)

---

## Not Changed

- The D1 close-fill cash accounting fix from 2.0.0 stands: realized P&L is still
  never added to cash on top of the trade proceeds.
- Commissions still stay out of a position's cost basis; `average_cost` remains
  a clean per-unit price.
- No package was added, removed, or merged. The standalone-engine /
  integrated-path split from ADR-0009 is unchanged.

---

## Known Limitations

- **The OMS order book is the execution path's remaining super-linear term.**
  `OrderBook.add` / `.replace` copy the whole order dict and
  `OMSEngine._update_sets` copies both order-id frozensets, once per stored
  order. This is a persistent-map problem, not event accumulation, and was
  deliberately left out of scope. `benchmarks/benchmark_execution_pipeline.py`
  measures it.
- **`OMSState` cannot be JSON-serialized as a whole state**, on 2.1.0 exactly as
  on 2.0.0: `OrderBook` keys orders by the `OrderId` dataclass, which neither
  `asdict` nor `json.dumps` accepts as a mapping key. Its history logs serialize
  correctly; the limitation is the typed identifier, not the log.
- **A risk-rejected request does not release its allocation reservation**, so
  `notional_allocated` over-reports after a risk rejection. Pre-existing; it
  does not gate trading, because the budget check reads
  `available_global_capital`.
- `dataclasses.asdict` cannot be extended, so it still returns an
  `AppendOnlyLog` for a history field. `alphalab.common.dataclass_to_dict` and
  `alphalab.persistence.serialize` are the supported serialization boundary.
- **Valuation is single-currency.** `PortfolioValuation` and `NAVCalculator`
  value the base currency only.
- **`_trade_record` still hard-codes** `sector_id="UNCLASSIFIED"` and
  `holding_period_seconds=0.0` ("D3", deferred).

---

## Quality Gates

`ruff check`, `ruff format --check`, `mypy` (strict, 843 source files), `pytest`
(1429 tests), `git diff --check`, `python -m build`, and `twine check dist/*`
all pass.

---

# [2.0.0] - 2026-09-04

## Overview

AlphaLab 2.0.0 is the v2 release line. It consolidates the v1.34.0–v1.46.0 engine
series, adds four new standalone packages, unifies the canonical execution-path
domain models, and fixes two portfolio/analytics defects found by an end-to-end
trading-research validation.

AlphaLab remains a library: there is no server, daemon, scheduler process, or CLI.
`alphalab.runtime.ExecutionPipeline` is the only spine that wires domain engines
together (market → strategy → allocation → risk → OMS → execution simulator →
portfolio → analytics); every other package is an independent, individually tested
engine that is not fused into a single runtime.

---

## Breaking Changes

Public symbols removed or changed. Import sites must be updated.

- **`alphalab.core.OrderRequest` is now the single proposed-order DTO.** The
  independent `alphalab.allocation.request.OrderRequest` /
  `alphalab.allocation.request.OrderSide` and the independent
  `alphalab.risk.models.OrderRequest` / `alphalab.risk.models.OrderSide` are
  removed. `alphalab.allocation.request` no longer exists. `alphalab.risk.models`
  now contains only `RiskViolation`. `OrderSide` is no longer part of the
  `alphalab.allocation` or `alphalab.risk` public API — use
  `alphalab.core.enums.Side`.
- **`alphalab.core.enums.Side` is the canonical order direction** everywhere on
  the execution path (`OrderRequest`, `oms.order.Order`, `Fill`, `Trade`, risk
  and allocation checks). `BUY`/positive and `SELL`/negative semantics are
  unchanged.
- **`alphalab.oms.order.Order` is the canonical lifecycle order.** The
  `alphalab.core.order` module and the `alphalab.core.Order` re-export are
  removed, along with `oms.order.Order`'s unused adapter surface
  (`to_core_order`, `from_core_order`, `canonical_order`). Every
  `oms.order.Order` field, property, and lifecycle transition is unchanged.
- **`Fill.filled_at` and `Trade.executed_at` are now `float`** (Unix seconds),
  matching every other timestamp on the execution path. They were previously
  timezone-aware `datetime`; the tz-aware `__post_init__` guard is removed. In
  `dataclasses.asdict` output these fields are now numbers, not `datetime`
  objects.
- **Removed proven-dead `alphalab.core` symbols:** `OrderCompat`, `Event`
  (`core/event.py`), `Signal` (`core/signal.py`), and the
  `alphalab.core.portfolio` / `alphalab.core.position` re-export shims. Use
  `alphalab.portfolio.engine.PortfolioState` and
  `alphalab.portfolio.position.Position` directly. `core.ids`
  (`PortfolioId` / `PositionId` / `SignalId` / `new_*`) is unaffected.
- **Package version** is `2.0.0`; the `alphalab.common.version` fallback and the
  `Development Status` classifier (`4 - Beta`) are updated to match.

---

## Added

### New packages (PR047–PR050)

- **Model Registry** (`alphalab.model_registry`) — versioned registration of
  trained model artifacts, `NONE → STAGING/PRODUCTION/ARCHIVED` promotion with an
  immutable promotions log, production rollback, and deployment metadata. Links
  to `alphalab.experiment_tracking` runs by id. State threaded functionally
  through immutable `ModelRegistry` values; no on-disk serialization.
- **AI Research Assistant** (`alphalab.research_assistant`) — deterministic,
  offline (no LLM, no network) grid-search research driver: candidate generation
  over a parameter grid, evaluation via a caller-supplied evaluator, best-first
  ranking, Markdown reporting, a one-call `run_research_workflow`, and a bridge
  that lifts a chosen candidate into an `alphalab.studio` `StrategyDefinition`.
- **Deployment Manager** (`alphalab.deployment_manager`) — packaging of
  strategy/model stacks into versioned, SHA-256-checksummed `ReleasePackage`s
  (manifest only, no artifact bytes), an append-only ledger of which release is
  active per environment, and previous-release rollback.
- **AlphaLab Enterprise** (`alphalab.enterprise`) — deterministic in-memory
  governance layer over one immutable `EnterpriseState`: session-lifecycle
  identity (no credentials accepted or stored), RBAC, append-only audit log,
  multi-user workspaces, secret *references* + rotation metadata (no secret
  values), and a compliance snapshot.

### Engine series (v1.34.0 – v1.46.0, PR034–PR046)

Delivered on `main` before the v2 line and included in 2.0.0: feature store,
factor library, options engine, futures engine, crypto engine, macro engine,
alternative data engine, machine learning engine, deep learning engine,
reinforcement learning engine, cloud research engine, cluster scheduler, and
experiment tracking. Each is a standalone deterministic package with its own
tests and benchmark.

---

## Changed — canonical execution domain models (R1–R4)

- **R1** — unified allocation/risk `Side` and `OrderRequest` into
  `alphalab.core` (see Breaking Changes). `alphalab.runtime.execution_pipeline`
  no longer converts requests field-by-field across the allocation → risk → OMS
  boundary; the `_risk_request` / `_core_side` bridges and
  `execution_adapters.core_side_from_oms` are deleted.
- **R2** — removed the five proven-dead `alphalab.core` symbols (see Breaking
  Changes). Implementations deleted outright; no aliases left behind.
- **R3** — retitled `alphalab.oms.order` as the canonical lifecycle order and
  removed its dormant adapter hooks; deleted `alphalab.core.order`. The order the
  execution pipeline produces is byte-for-byte identical.
- **R4** — `Fill.filled_at` / `Trade.executed_at` changed from `datetime` to
  `float`. `execution_adapters.canonical_execution_from_report` now passes
  `report.timestamp` straight through instead of
  `datetime.fromtimestamp(..., tz=UTC)`.

---

## Fixed

- **D1 (critical) — portfolio close/reduce cash accounting.**
  `alphalab.portfolio.engine.PortfolioEngine.apply_fill` computed
  `cash_impact = -(quantity * price) - commission + pnl`. On a closing or
  reducing fill the `+ pnl` term double-counted realized P&L into cash —
  overstating cash / NAV / `PerformanceReport.ending_capital` /
  `RiskState.current_nav` on wins and understating them on losses. The `+ pnl`
  term is removed; position cost-basis math and the
  `PositionReduced` / `PositionClosed` event payloads are unchanged. Guarded by
  new unit tests (winning, losing, and partial-reduction round-trips) and
  `tests/regression/test_close_fill_cash_accounting.py`, which drives the real
  `ExecutionPipeline` open → hold → close and asserts
  `NAV == ending_capital == risk.current_nav == starting_cash + realized_pnl - commissions`.
- **D2 (medium) — `PerformanceReport` serialization.**
  `alphalab.analytics.attribution.calculate_attribution` wrapped
  `AttributionMetrics` fields in `types.MappingProxyType`, which
  `alphalab.persistence` (via `dataclasses.asdict`) cannot copy
  (`cannot pickle 'mappingproxy' object`). It now returns ordinary `dict`s, like
  every other frozen dataclass in AlphaLab. Field names and the
  `Mapping[str, Decimal]` annotations are unchanged.

---

## Build / Packaging

- The release build and distribution validation were aligned with **Core
  Metadata 2.5 / Twine 7**. Hatchling 1.30 emits `Metadata-Version: 2.5`
  (PEP 639), which Twine 6 rejects; the dev toolchain pin is now
  `twine>=7.0,<8`, and the redundant unpinned `pip install build twine` step
  was dropped from the CI and release workflows so they use the pinned
  toolchain. `python -m build` and `twine check dist/*` pass for the 2.0.0
  wheel and sdist. Hatchling is unchanged; no application dependency changed
  (`dependencies = []`).

---

## Known gaps (unchanged in 2.0.0)

- `alphalab.runtime.execution_pipeline._trade_record` still attributes realized
  P&L by scanning portfolio events in reverse and hard-codes
  `sector_id="UNCLASSIFIED"` and `holding_period_seconds=0.0` (deferred — "D3").
- Mark-to-market position repricing is not implemented.
- `alphalab.replay` is a standalone engine; it does not drive `ExecutionPipeline`.
- `alphalab.data.feed.Bar` and `alphalab.market.bar.Bar` are separate,
  incompatible models (a third `Bar` lives in `alphalab.marketdata.feed`).
- The `broker` / `brokers`, `marketdata` / `data` / `feed`, `kernel`, and
  `core/events` areas remain intentionally unresolved / product-surface
  decisions.

---

## Quality gates

- ruff check, ruff format --check, mypy --strict (833 source files) — clean
- pytest — 1221 passed (1207 unit, 4 integration, 10 regression)
- `git diff --check` — clean

---

# [1.0.0] - 2026-07-05

## First Stable Release

AlphaLab 1.0.0 is the first stable public release of the framework.

This release establishes the core architecture for deterministic quantitative research, systematic strategy development, portfolio optimization, historical replay, production runtime management, and institutional research workflows.

---

## Added

### Core Framework

- Immutable domain models
- Deterministic engine APIs
- Event-driven architecture
- Shared validation utilities
- Shared registry utilities
- Common infrastructure package
- Python 3.12 support
- Strict static typing throughout the framework

### Research

- Research Engine
- Statistical research workflows
- Strategy evaluation
- Research payload validation

### Strategy Runtime

- Strategy lifecycle management
- Event dispatch
- Runtime supervision
- Context abstraction
- Intent validation

### Universal Data Engine

- Canonical datasets
- Dataset metadata
- Schema validation
- Data quality reporting
- Timeframe conversion
- Dataset cataloguing

### Replay Engine

- Historical event replay
- Deterministic market simulation
- Timeline reconstruction

### Portfolio Optimizer

- Capital allocation
- Equal Weight optimization
- Minimum Variance optimization
- Maximum Sharpe optimization
- Inverse Volatility optimization
- Portfolio constraints
- Exposure analysis
- Transaction cost estimation
- Portfolio rebalancing

### Broker Integrations

- Provider abstraction
- Paper Trading
- Alpaca integration architecture
- Interactive Brokers integration architecture
- Zerodha integration architecture
- Authentication workflows
- Connection management

### Production Runtime

- Runtime supervision
- Health monitoring
- Checkpointing
- Recovery workflows
- Runtime metrics

### Strategy Studio

- Project management
- Strategy registration
- Research sessions
- Pipelines
- Reports
- Workspace management
- Backtest orchestration

### AlphaLab Workbench

- Unified workspace
- Project management
- Research orchestration
- Dataset management
- Dashboard infrastructure

---

## Documentation

Added comprehensive documentation including:

- README
- Getting Started Guide
- Architecture Guide
- System Design
- Engineering Guidelines
- Architectural Decision Records (ADRs)
- Examples documentation
- Contributing Guide

---

## Examples

Added ten fully synchronized runnable examples covering:

1. Research Engine
2. Strategy Runtime
3. Replay Engine
4. Market Data
5. Broker Integrations
6. Portfolio Optimizer
7. Universal Data Engine
8. Strategy Studio
9. Workbench
10. Complete end-to-end workflow

---

## Engineering

Improved overall project quality through:

- Shared validation infrastructure
- Shared registry utilities
- Common package refactoring
- Consistent immutable APIs
- Packaging improvements
- Version alignment
- Example synchronization
- Release engineering

---

## Quality Assurance

Validated with:

- ✅ 583 passing unit tests
- ✅ Strict MyPy (631 source files)
- ✅ Ruff clean
- ✅ Python package build
- ✅ Wheel validation
- ✅ Source distribution validation
- ✅ Twine package verification

---

## Packaging

AlphaLab 1.0.0 is distributed as:

- Source Distribution (`sdist`)
- Universal Python Wheel (`py3-none-any`)

---

## Notes

This release establishes the stable architectural foundation of AlphaLab.

Future releases will expand the framework with additional quantitative research capabilities while maintaining backward compatibility wherever practical.