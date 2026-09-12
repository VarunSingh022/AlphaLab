# ADR-0030: The Run Runtime and the Final Driver Boundary

## Status

**Accepted and implemented in v2.14.0.** `alphalab.runtime.run` ships
`RunConfig`, `RunState`, `RunStep`, `SkippedRecord` and `RunEngine`;
`alphalab.runtime.run_snapshot` ships `RUN_SNAPSHOT_SCHEMA = 1` with
`RunSnapshot`, `RunObjects` and the `capture` / `restore` / `from_primitives`
trio. `SessionState`, `SessionConfig`, `BacktestState`, `BacktestConfig` and
`BacktestStep` are removed; `alphalab.runtime.session_snapshot`,
`alphalab.backtesting.snapshot` and `alphalab.backtesting.config` are deleted;
`SESSION_SNAPSHOT_SCHEMA` and `BACKTEST_SNAPSHOT_SCHEMA` are retired. Nothing is
aliased. `TradingSession`, `BacktestEngine` and `ReplayBacktest` are stateless
drivers over `RunState`.

**Discharges ADR-0023 decision 1's deferral** — the reshape it split the run
envelopes to permit, and which ADR-0029 was scoped to survive. Both predictions
held: the run layer moved, `PIPELINE_SNAPSHOT_SCHEMA` stayed at 2, and
`alphalab/persistence/` changed not one line of code.

Depends on **ADR-0022** for the identifier position continuation reads, on
**ADR-0014** for how a live object crosses a snapshot boundary, on **ADR-0017**
for input identity as a caller-supplied opaque value, on **ADR-0024** for what a
runtime may not believe about a venue, and on **ADR-0029** for the durability
boundary this decision deliberately cannot reach.

Two things are worth recording because they were not obvious when this was
planned. `BacktestStep` turned out not to be a backtest concept at all — every
field of it is derived from a record and an `ExecutionPipelineResult`, so it
became `RunStep` and a session records one per record too. And the archaeology's
proposed `RunDriver` protocol was **not** shipped: the three drivers' `run`
signatures genuinely differ in input, clock and result type, so a protocol would
have covered only the three methods that are already one-line delegations.

---

# Context

Measured against the working tree at v2.13.0, the run layer was implemented
twice and the duplication was not in the state fields.

`SessionState` and `BacktestState` shared four field *names* — `config`,
`pipeline`, `processed`, `current_timestamp` — three of which are scalars. What
was actually duplicated was the **run-driver contract**:

| Contract | `TradingSession` | `BacktestEngine` |
| --- | --- | --- |
| `initialize` | `ExecutionPipeline.initialize(config.pipeline, …)` | the same expression |
| `advance` | `ExecutionPipeline.process_record(…)` plus bookkeeping | the same call, different bookkeeping |
| `resume` | `use_id_source(id_source_for(state.pipeline.id_position))` | **character-identical** |
| `run` | `with id_scope(config.seed): init; loop` | the same shape |
| `capture` | nests `capture_pipeline`, re-derives 6 shared fields | the same, for the same 6 |
| objects | `SessionObjects(pipeline, fill_policy)` | `BacktestObjects(pipeline, fill_policy)` |

Two owners of one determinism contract is a contract that can drift.

Three further findings decided the shape:

- **A third driver already existed.** `alphalab.reinforcement_learning`'s
  `TradingEnvState` is `(config, pipeline, own bookkeeping)` — the same shape,
  arrived at independently. The repository had converged on the driver pattern
  three times without naming it.
- **One fact had two homes and two durability answers.** `SessionState.source_id`
  was on the state and was captured. `dataset_id` was an argument to
  `finalize`, landed on `BacktestResult`, and **no snapshot carried it** — so a
  backtest that stopped and continued in another process lost the identity
  `derive_evidence` refuses to invent.
- **Two `ExecutionMode` members were dead.** `BACKTEST` and `REPLAY` were set by
  nothing in the package, because only a session carried a mode. A captured
  backtest and a captured replay wrote identical payloads.

Measurement decided where the state could *not* go. `ExecutionPipelineState` is
a 16-field frozen `slots` dataclass reconstructed **11 times per trading record**
(3 times per quiet record), instrumented by wrapping `dataclasses.replace`.
Widening it costs that eleven times over: 16 → 20 fields is **+5.6%** of a
220 µs record and 16 → 24 is **+14.0%**. Meanwhile the whole run layer costs
1.3–2.1 µs per record — under 1% of the hot path.

---

# Decision drivers

- **D1.** One contract, one owner. A determinism guarantee with two
  implementations is the defect, not the duplication of source.
- **D2.** Do not widen the hot state. The core is reconstructed eleven times per
  trading record and the run layer is reconstructed once.
- **D3.** Do not move a schema for convenience — the rule ADR-0029 D5 applied.
- **D4.** Remove duplicate ownership rather than hide it. No alias, no facade.
- **D5.** A boundary that must survive v3.0 cannot leave a known refactor behind
  it.
- **D6.** Prefer the repository's own precedent to a new mechanism.

---

# Decision

## 1. Two tiers, one owner each

```text
TIER 3  drivers        TradingSession   MarketDataSource + a clock reading
                       BacktestEngine   MarketDataset
                       ReplayBacktest   alphalab.replay's cursor
                       (later)          StreamingDriver, LiveDriver

TIER 2  the run        RunEngine / RunState / RunConfig
                       initialize · publish_record · advance · resume · finalize

TIER 1  the step       ExecutionPipeline / ExecutionPipelineState   FROZEN
                       market → strategy → allocation → risk
                       → OMS → execution → portfolio → analytics
```

**A driver decides which record comes next and what clock reading to judge it
by, and shapes a finished `RunState` into its own result type.** It holds no
execution state and no run state. That is the whole definition, and it is why a
streaming driver and a live driver are later entries in the same table rather
than a redesign.

`alphalab.reinforcement_learning` stays on Tier 1 directly. It has no records
and no run cursor — it drives episodes — and forcing it onto Tier 2 would be
symmetry for its own sake. That it does *not* fit is what shows Tier 2 is a
boundary rather than a wrapper.

## 2. `RunState` is eight fields, and four of them are the continuation

```text
RunState        config  pipeline
                processed  current_timestamp  last_record_timestamp   ← continuation
                source_id                                             ← provenance
                steps  skipped                                        ← observability
```

The continuation set is what `capture → store → fresh process → restore →
resume → continue` needs, and it is four fields plus the config. `source_id`,
`steps` and `skipped` are carried because ADR-0023 decision 8's Class 1 includes
them, not because a decision reads them.

`RunConfig` is ten fields: the union of what the two configs carried. This is
not a merger of two things that looked alike — **every field is read by a
run-level function `RunEngine` owns.** `ordering` and
`max_market_data_age_seconds` are read by `advance`; `years_elapsed`,
`risk_free_rate` and `compile_analytics` by `finalize`. That only one of the two
old drivers exposed each was an accident of which was written first: a session
that wants a performance report needs exactly the three `finalize` reads.

`mode` is **required**. A run that cannot say which environment it is in cannot
say what its captured state means.

## 3. `advance` is the canonical step, and its order is the contract

```text
staleness gate  →  ordering gate  →  ExecutionPipeline.process_record
                →  append RunStep  →  update the cursor
```

The union of what the two drivers did, in that order. A stale record or a
regressing record returns `(state, None)` or raises, exactly as the session's
did; a processed record appends a `RunStep`, which the backtest's did.

## 4. The clock is driver-owned and injected per step

There are **zero wall-clock reads in `alphalab/runtime/` and
`alphalab/backtesting/`**. Every timestamp on the path comes from
`event.timestamp`, `record.timestamp` or `report.timestamp`. The only clock in a
run is the `now` argument to `advance`, supplied by the driver, defaulting to
the record's own timestamp, and **never stored** — not on `RunState`, not on
`RunConfig`, not in the envelope.

This decision was already correct in the code; v2.14 names it. It is also what
makes streaming and live additive: they differ from a backtest in which reading
they pass, not in what the runtime does with it.

## 5. `resume` has exactly one implementation

```python
return use_id_source(id_source_for(state.pipeline.id_position))
```

`RunEngine.resume`, and nowhere else. An AST walk over `alphalab/` finds exactly
one `id_source_for(` call site, and a regression test holds that count at one.
`TradingSession.resume` and `BacktestEngine.resume` delegate.

## 6. One run envelope, and the core does not move with it

```text
+ RUN_SNAPSHOT_SCHEMA      = 1     alphalab.runtime.run_snapshot
- SESSION_SNAPSHOT_SCHEMA          retired with the state it versioned
- BACKTEST_SNAPSHOT_SCHEMA         retired with the state it versioned

  PIPELINE_SNAPSHOT_SCHEMA = 2     UNCHANGED, and this is the point
  ALLOCATION = 1   OMS = 1   PORTFOLIO = 2   LIFECYCLE = 1
  RUN_STATE_ENVELOPE = 1   DEFAULT_SCHEMA_VERSION = 1      all UNCHANGED
```

`RunSnapshot` nests `PipelineSnapshot` unchanged and `ExecutionPipelineState`
gains no field. This is exactly the churn confinement ADR-0023 decision 1
separated the envelopes to buy, now spent as designed.

## 7. No cross-version read, and the reason is per envelope

`RUN_SNAPSHOT_SCHEMA` is new and reads nothing older. The house rule in
`require_schema_version` is one version per subsystem with no migration path,
and its three precedents turn on one question — *does the old payload lack
anything?*

| v2.13 payload | Lacks | Honest value? |
| --- | --- | --- |
| `SessionSnapshot` | `years_elapsed`, `risk_free_rate`, `compile_analytics` | **No.** These are dataclass defaults, and `require`'s stated rule is that a missing field is never filled in with one. Reading them would invent analytics parameters the writer never stated. |
| `BacktestSnapshot` | `mode` | **No.** A backtest run and a replay run wrote identical payloads, so the label is unknowable. (`last_record_timestamp` and `source_id` *are* honestly derivable, which is why `mode` is decisive.) |

Both are refused. This is the portfolio precedent, not the OMS one, and it is a
decision rather than an omission. A v2.13 run payload is read by the v2.13 build
that wrote it.

The blast radius was measured before the decision: ADR-0029's own Context
records that **no production module imports the three snapshot modules — their
only callers are tests** — and the store that gives those payloads somewhere to
live shipped one release earlier. This is the cheapest moment this move will
ever have, and every later release makes it dearer.

## 8. `source_id` is the one home for input identity

`RunState.source_id` is set by whichever driver started the run — a source's
`source_id`, a dataset's `dataset_id` — and is carried by the run snapshot.
`BacktestResult.dataset_id` **reads through it**, and `finalize` no longer takes
a dataset argument.

The two *guarantees* ADR-0017 kept apart stay apart, which was the real point:
a `MarketDataset` validates its ordering on construction while a
`MarketDataSource` merely declares one, and what a run will accept is
`RunConfig.ordering` — stated where the gate that enforces it reads it, rather
than smuggled inside an identifier's type.

This closes a defect rather than moving a field: before v2.14 a restored
backtest reached `derive_evidence` with no dataset identity at all.

## 9. `BacktestResult` is a projection, not a second authority

One field, `run: RunState`. `config`, `state`, `steps`, `records_processed`,
`seed`, `dataset_id`, `orders`, `fills`, `trades`, `equity_curve`, `valuation`,
`report` and `unpriced_assets` are all read-through properties. There is no
second copy of any fact to fall out of date.

## 10. Replay is a driver, and keeps its own cursor

`ReplayBacktest` drives `RunEngine.advance` and declares
`ExecutionMode.REPLAY`. `ReplayState` stays exactly as it was: a cursor with its
own identifier stream at `seed + REPLAY_CURSOR_SEED_OFFSET`, sharing one field
*name* with `RunState` (`current_timestamp`) and nothing else. No second run
envelope is introduced.

**Replay resumability remains deferred.** Closing it needs a cursor snapshot and
a second `IdStreamPosition`, both additive to this driver, reaching neither
`RunState` nor `RUN_SNAPSHOT_SCHEMA`.

## 11. The orphan runtime and `alphalab.production` are deprecated, not removed

Ten modules in `alphalab.runtime` — `engine`, `dispatcher`, `supervisor`,
`events`, `validation`, `state`, `views`, `metrics`, `runtime`, `lifecycle` —
implement a lifecycle state machine with heartbeats that has **zero production
importers** and never drove the execution path. They are deprecated for a second
and decisive reason: their names collide with the canonical ones and the
canonical ones lose. `from alphalab.runtime import create_runtime` returns the
dead function while every harness calls
`alphalab.strategy.runtime.create_runtime`; `RuntimeState` here is a heartbeat
record while the one the pipeline threads is `alphalab.strategy.state.RuntimeState`.
A release that settles runtime ownership cannot ship `RunEngine` beside a
`RuntimeEngine` that means something else.

`alphalab.production` gets the same treatment: `Checkpoint` holds six opaque
strings it never decodes and `RecoveryEngine.recover` restores nothing, while
`ProductionState` is a third runtime-shaped state with its own `runtime_id`.

Both use the PEP 562 attribute-access mechanism `alphalab.persistence`
established, and for the same reason: `alphalab.runtime` *is* the execution
path, so an import-time warning would fire on every consumer of
`ExecutionPipeline` to deprecate names that path never uses. **Nothing is
removed in v2.14**; both join `REMOVAL_RELEASE` for v3.0.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Execution step and its state | `ExecutionPipeline` / `ExecutionPipelineState` — frozen |
| The run, and its cursor | **`RunEngine` / `RunState`** |
| Run configuration | **`RunConfig`** |
| Input, and the clock reading | **the driver** |
| Continuation | **`RunEngine.resume`** — one implementation |
| Run capture and restore | `alphalab.runtime.run_snapshot` — free functions |
| Input identity | **`RunState.source_id`** |
| Run identity | the caller, supplied at `put` (ADR-0029) — never in a captured state |
| Durable persistence | `RunStateStore` — unchanged, and unreachable from here |
| Strategy-internal state | the strategy, via `StrategyStateProtocol` (ADR-0025) |
| Identifier position | `ExecutionPipelineState.id_position` (ADR-0022) |
| Replay cursor | `ReplayState` — the replay driver's, not the run's |
| Venue belief | not persisted (ADR-0024); threaded by a future live driver |
| Deployment and governance | `alphalab.lifecycle` — outside the runtime |

---

# Data model changes

```text
+ RunConfig      10 fields    alphalab.runtime.run
+ RunState        8 fields
+ RunStep         7 fields    (BacktestStep, moved and renamed to what it is)
+ SkippedRecord              (moved from runtime.session)
+ RunEngine                  initialize / publish_record / advance / resume / finalize
+ RunSnapshot, RunObjects    alphalab.runtime.run_snapshot
+ RUN_SNAPSHOT_SCHEMA = 1
+ REMOVAL_RELEASE entries    alphalab.runtime.orphan, alphalab.production

- SessionState, SessionConfig, SessionSnapshot, SessionObjects
- BacktestState, BacktestConfig, BacktestStep, BacktestSnapshot, BacktestObjects
- SESSION_SNAPSHOT_SCHEMA, BACKTEST_SNAPSHOT_SCHEMA
- alphalab.runtime.session_snapshot, alphalab.backtesting.snapshot,
  alphalab.backtesting.config
                             removed, not aliased

  BacktestResult             now one field; every view reads through
  ExecutionPipelineState     UNCHANGED — 16 fields
  PIPELINE / ALLOCATION / OMS / PORTFOLIO / LIFECYCLE /
  RUN_STATE_ENVELOPE / DEFAULT constants        UNCHANGED
  RunStateStore, RunStateRef                    UNCHANGED
  StrategyProtocol, StrategyStateProtocol, StrategyContext   UNCHANGED
```

---

# Boundary behavior

Refuse an unknown run-envelope version → refuse an unknown pipeline version →
require and type-check every supplied live object → rebuild each subsystem
through its own `restore` → re-assert construction-time invariants → return
state. Nothing partial is returned by a refused restore.

`restore` installs no identifier source and processes no record. Continuation is
`RunEngine.resume` plus `advance`, between completed steps, which is where
`id_position` is accurate.

---

# Persistence semantics

One coordinated release: one constant created, two retired, six unchanged.
`alphalab/persistence/` has **no code change** — measured as an empty
`git diff` over the package, and asserted by a regression test that walks
`run_store.py`'s AST and finds no domain type bound or called there.

A complete `capture` / `restore` cycle inside `id_scope` advances
`current_id_position().draws` by **exactly zero**, matching ADR-0029 decision 7
for the store on either side of it.

---

# Testing invariants

1. `restore(capture(s), objects) == s` for an empty run, a populated run and a
   fully consumed run.
2. Every `RunState` bookkeeping field survives the round trip, enumerated — and
   a meta-test fails if a future field is not enumerated.
3. `RunConfig` survives field by field; the fill policy by type (ADR-0014).
4. Skipped records survive exactly, with the record itself.
5. A restored run still refuses a regressing record, and a restored `UNORDERED`
   run still skips and records one.
6. Neither `RunState`, `RunConfig`, `RunStep` nor `RunSnapshot` holds a
   clock-like field, and neither run module reads a wall clock.
7. `capture` and `restore` each draw zero identifiers.
8. No `run_id` appears on any of the three types.
9. **Cross-process byte identity**: a seeded run of N of N+M records, captured,
   serialized, `put`, read back in a *separate interpreter*, restored, resumed
   and advanced over the remaining M, produces a final serialized payload
   byte-identical to an uninterrupted N+M run.
10. Each driver declares its own mode and records the input it read.
11. Replay's cursor shares exactly one field name with `RunState` and never
    reaches the envelope.
12. Exactly one `id_source_for` call site exists in `alphalab/`.
13. `ExecutionPipelineState` has exactly 16 fields and
    `PIPELINE_SNAPSHOT_SCHEMA == 2`.
14. A v2.13 session payload and a v2.13 backtest payload are each refused,
    naming what they lack.
15. Every retired name is absent *and* un-importable; the PEP 562 hook serves
    none of them.

---

# Migration and compatibility

No migration framework, consistent with `require_schema_version`'s house rule.
No compatibility aliases and no re-exports of removed names.

The public break is narrow and documented: `SessionState`, `SessionConfig`,
`BacktestState`, `BacktestConfig`, `BacktestStep`, both snapshot modules and
both run schema constants. The repository's versioning policy permits this —
*"minor releases may still make small, documented breaking changes to a narrow
public API where correctness requires it"* — with four precedents, the closest
being v2.3.0, which renamed `alphalab.brokers`' fields "so both broker packages
speak one vocabulary". Measured blast radius: test, example and benchmark files
only, and **zero production modules**.

---

# Explicit non-goals

- **No streaming implementation.** `MarketDataSource` is already the interface;
  a stream is a new source plus a new driver.
- **No live venue transport.** The contract, routing gates, mapping and
  fill-return path exist and are tested; the transport is not in this
  repository.
- **No FX and no multi-currency valuation.**
- **No artifact storage**; nothing in the tree produces artifact bytes.
- **No governance, RBAC or approval workflow.** Lifecycle stays outside.
- **No replay resumability** (decision 10).
- **No removal** of any deprecated package.
- **No `RunDriver` protocol.** Considered and rejected — see Alternatives.
- **No change to `RunStateStore`, `ExecutionPipelineState`, the strategy
  packages, or `alphalab.reinforcement_learning`.**
- **No migration framework, compatibility alias, facade or silent fallback.**

---

# Consequences

Benefits. Continuation has one owner where it had two identical ones. A run's
input identity survives a stop and a restart, so a restored backtest can still
derive evidence. `ExecutionMode` is honest for all four environments. One run
envelope and one schema constant replace two of each. And the seam for a live
driver is an addition to a named contract rather than a change to it.

Costs. The public break above is real, and a v2.13 run payload now needs a
v2.13 interpreter forever. `RunConfig` gives every driver ten fields when a
given driver may read seven. A session's captured payload grows — 8.24 MB to
9.35 MB over 1,000 records — because it now records a `RunStep` per record, as a
backtest always did; the hot path is unaffected, measured at +0.05% against the
Phase 1 reading relative to an in-process pipeline control.

Deferred, and stated so it is not discovered later: replay is still not
resumable while session and backtest are, and no live driver exists.

---

# Alternatives Considered

**A `UnifiedRuntime` wrapping the pipeline, with thin Session/Backtest
adapters.** Rejected. It adds a third object without removing either of the two
it wraps, so three places could hold a run's state and two `resume`
implementations would still exist underneath. v3.0 would have had to delete the
adapters and promote the wrapper — precisely the refactor this release exists to
remove — and both run schema constants would have stayed, deferring ADR-0023's
churn once more.

**Folding the run fields into `ExecutionPipelineState`.** Rejected on
measurement. The state is reconstructed 11 times per trading record by design,
so the merge costs +5.6% to +14.0% of a record for fields no engine reads, and
it moves `PIPELINE_SNAPSHOT_SCHEMA` to 3 — the schema-move-for-convenience
ADR-0029 D5 forbids — destroying the one property ADR-0023 decision 1 bought.

**Shared free functions with both states kept.** Rejected. It removes the copied
source and leaves the copied *ownership*: two states, two envelopes, two
constants and the `source_id`/`dataset_id` asymmetry all survive, and v3.0
inherits the pending reshape.

**A `RunDriver` protocol.** Rejected as speculative. The three drivers' `run`
methods differ in input (`MarketDataSource` / `MarketDataset` / a cursor), in
clock, and in result type (`RunState` / `BacktestResult` / `ReplayResult`), so a
protocol could only cover `initialize`, `advance` and `resume` — which are
already literal one-line delegations to `RunEngine`. It would document a
similarity rather than enforce a constraint.

**Keeping `SessionState` and `BacktestState` as aliases of `RunState`.**
Rejected. That is the compatibility bridge this decision exists to avoid: it
would let old code keep two names for one authority and leave a removal for
v3.0 to perform.

**Migrating v2.13 payloads.** Rejected under decision 7: no honest value exists
for what they lack, and inventing one contradicts `require`'s stated rule.

**Forcing `alphalab.reinforcement_learning` onto Tier 2.** Rejected. It has no
records and no run cursor. Symmetry is not a reason to give an episode driver a
record cursor it would leave at zero.

---

# Release impact

Minor-version feature with one narrow, documented public break. One schema
constant created, two retired, six unmoved. `alphalab/persistence/`,
`alphalab/runtime/execution_pipeline.py`, `alphalab/runtime/snapshot.py`,
`alphalab/strategy/` and `alphalab/reinforcement_learning/` are behaviourally
unchanged. Two packages join the v3.0 removal schedule and nothing is removed.

**v3.0 readiness.** The v2.14 runtime boundary is the final runtime boundary;
v3.0 does not need to redesign runtime ownership. One seam remains and it is
named: no live driver exists. Adding one is an entry in the Tier 3 table that
threads `BrokerState` and `ExternalOrderMap` itself and calls `route_order`,
`apply_execution_report` and `apply_terminal_outcome` — all of which exist and
are tested, and none of which changes `RunState`, `RunEngine`,
`RUN_SNAPSHOT_SCHEMA` or the store.
