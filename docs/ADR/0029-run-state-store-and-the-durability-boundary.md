# ADR-0029: The Run-State Store and the Durability Boundary

## Status

**Accepted and implemented in v2.13.0.** `alphalab.persistence` ships
`RunStateRef` and `RUN_STATE_ENVELOPE_SCHEMA` (`run_state.py`), and the
`RunStateStore` protocol with `FileRunStateStore` and `MemoryRunStateStore`
(`run_store.py`). The nine placeholder-store modules are deprecated through a
PEP 562 hook on the package, and removed in v3.0. `alphalab.common.serialization`
resolves `__serializable__` on the type rather than through a
`runtime_checkable` protocol check, which the encoder measurements below
required. Every decision shipped as written; nothing was softened after
implementation.

The evidence is in three suites.
`tests/regression/test_persistence_draws_from_the_run_stream.py` characterizes
the defect decision 7 exists for -- the legacy store advancing a run's `draws`
0 -> 2, and consuming that run's own next two identifiers.
`tests/regression/test_run_state_store.py` holds the boundary contract across
both backends. `tests/regression/test_durable_run_state_cross_process.py`
holds the oracle: a seeded run of five records here, written to disk, continued
in a **separate interpreter** over six more, produces a final serialized payload
**byte-identical** to an uninterrupted eleven-record run.

Two things are worth recording because they were not obvious when this was
written. The store needed **no new exception type**: `PersistenceValidationError`
and `StorageError` already live in the codec spine, which survives the v3.0
removal, so inventing a parallel hierarchy would have created a surface to
retire later. And `FileRunStateStore` requires its root to **already exist**
rather than creating it -- a store that silently materializes directories is a
smaller version of the silent fallback decision 5 refuses.

Completes **ADR-0023**, which built the run-state envelope and left it with
nowhere to go: `capture` produces a projection, `serialize` produces a string,
and nothing in `alphalab` writes that string anywhere. Depends on **ADR-0022**
for the identifier cursor a stored state carries, on **ADR-0024** for what is
deliberately never persisted, on **ADR-0025** for the strategy state a payload
holds, and on **ADR-0017** for the doctrine this decision applies to run
identity.

Follows the boundary shape **`alphalab.marketdata.transport`** already
established and shipped: a narrow protocol, one real implementation, and one
deterministic double the caller constructs by name.

---

# Context

Measured against the working tree at v2.12.0, the durable-state work is
complete on one side of a line and absent on the other.

Complete: `ExecutionPipelineState`, `SessionState` and `BacktestState` all have
`capture` / `restore` / `from_primitives`; seven schema constants version the
payloads; `StrategyStateProtocol` gives a strategy a two-sided codec for its own
memory; `IdStreamPosition` carries the identifier cursor as data; and `restore`
re-asserts the construction-time invariants `initialize` enforces. A stopped run
can be described exactly and rebuilt exactly.

Absent: anywhere to put the description.

- There are **zero filesystem calls** in `alphalab`. The one I/O call in the
  package is `urllib.urlopen`, in `marketdata/transport.py`.
- `MemoryStorage` is the only implementation of `PersistenceProtocol`. It has no
  run identity, no sequence, no "latest for this run", and no decoder for its
  own `PersistenceState` — the write direction exists and the read direction
  does not.
- **No production module imports** `runtime.snapshot`,
  `runtime.session_snapshot` or `backtesting.snapshot`. Their only callers are
  tests.

So the gap v2.13 closes is not serialization, not schema, and not a missing
restore boundary. It is that **durability has no owner** — and the one object in
the tree that claims to own it does not. `production.Checkpoint` holds six
opaque caller-supplied strings (`runtime_state`, `portfolio_state`,
`orders_state`, `positions_state`, `research_state`, `replay_state`);
`RecoveryEngine.recover` sets `is_running=True` and emits two events;
`RuntimeOperations.restore` emits one event. Nothing is restored. That is the
`marketdata` defect exactly — "silently fake data with no seam to ever make it
real" — in the one package whose name promises otherwise.

## The persistence package is two packages

Which half is canonical is not a matter of taste, and measurement settles it.
Production code outside `alphalab/persistence/` imports exactly three modules
from it:

| Half | Modules | Production importers |
| --- | --- | --- |
| Codec spine | `serializer`, `decode`, `exceptions` | **7** — every snapshot module |
| Store | `protocol`, `storage`, `state`, `engine`, `adapter`, `snapshot`, `views`, `validation`, `events` | **0** |

The codec spine is load-bearing and stays. The store half is a placeholder with
no production consumer, and it is what this decision replaces.

## Persistence consumes the run's identifier stream

`MemoryStorage._create_id()` calls `new_id()`, which reads the ambient
`_ID_SOURCE` contextvar. Every storage operation emits one system event and
therefore draws one identifier from whatever stream is installed.

Measured inside `id_scope(11)`:

```text
save_snapshot   +1 draw        load_events   +1 draw
append_event    +1 draw        clear         +1 draw
load_snapshot   +1 draw        statistics     0 draws
```

One `save_snapshot` plus one `append_event` advances `draws` **0 → 2**. The two
identifiers it consumed are not incidental: they are literally the run's own
next two identifiers. A run seeded at 11 that mints one id, persists, then
mints two more produces its 1st, 4th and 5th identifiers, while its 2nd and 3rd
are stamped on a `SnapshotSaved` and an `EventAppended`.

`load_snapshot` draws too, so **reading** a state back would shift the stream as
well. A durable store built on this protocol could not restore a run without
changing it.

This is the same class of defect ADR-0022 removed from resume, discovered on the
other side of the same boundary, and it decides the shape of the store rather
than merely needing a fix.

## The v3.0 constraint

v3.0 is an architecture-frozen release. ADR-0023 decision 1 records, in the
present tense, that `SessionState` and `BacktestState` are "the layer a future
integrated-runtime release is expected to reshape", and split the envelopes so
that reshape can move `SESSION_SNAPSHOT_SCHEMA` and `BACKTEST_SNAPSHOT_SCHEMA`
without versioning the stable pipeline core.

A persistence boundary designed now must therefore survive a runtime
unification that has not happened yet. That is not a hypothetical
future-proofing exercise; it is an already-documented, already-planned change to
the exact states this boundary would otherwise name.

---

# Decision drivers

- **D1.** Reuse the accepted boundary shape. `marketdata.transport` settled how
  an effectful dependency enters this codebase; this applies it rather than
  inventing a second answer.
- **D2.** A boundary that must survive v3.0 cannot name a type that is scheduled
  to be reshaped before v3.0.
- **D3.** Never silently fall back. A store that degrades to memory when a disk
  is unavailable reports success for a run that was not saved.
- **D4.** Do not build infrastructure without a current caller.
- **D5.** Do not move a schema constant for storage convenience — the rule
  ADR-0018 applied when it declined to admit governance actors into v2.7.
- **D6.** Persistence must not perturb the run it persists. A store that draws
  from the run's identifier stream changes the run by observing it.
- **D7.** Two store abstractions must not ship into a release that must be
  frozen.

---

# Decision

## 1. Durability has exactly one owner

`RunStateStore`, a protocol in `alphalab.persistence`, is the only thing in
AlphaLab that owns durable run state.

It lives in `alphalab.persistence` because the codec spine already does and the
dependency already runs domain → persistence. The store accepts and returns a
`str`, so it names no domain type and inverts nothing — the objection
`persistence/adapter.py` records against putting decoders here does not apply to
a store that never decodes.

Ownership elsewhere is unchanged and is restated here so the seam is explicit:

- **Capture and restore** stay with the module that owns the state —
  `runtime.snapshot`, `runtime.session_snapshot`, `backtesting.snapshot` — as
  free functions. Capture remains a pure function of the state handed to it and
  no state class grows a method.
- **Continuation** stays with `TradingSession.resume` and
  `BacktestEngine.resume`.
- **Strategy memory** stays with the strategy (ADR-0025).

## 2. The store is payload-agnostic

```text
put(run_id: str, sequence: int, payload: str) -> RunStateRef
get(ref: RunStateRef) -> str
latest(run_id: str) -> RunStateRef | None
list_runs() -> tuple[str, ...]
```

The store **must not name, import, inspect, parse, or depend on any snapshot
type**. It does not know whether a payload is a `PipelineSnapshot`, a
`SessionSnapshot`, a `BacktestSnapshot`, or something a later release invents.

This is D2 discharged. When runtime unification reshapes `SessionState` and
`BacktestState` and moves their two constants, it cannot reach this protocol,
because there is nothing here for it to reach. A store with
`save_session(state)` and `save_backtest(state)` methods would have to be
redesigned by that release; this one will not be.

It is also what keeps ADR-0023 decision 1's error-locality property intact: the
store never parses a payload, so a nested pipeline, OMS or portfolio version
failure still surfaces from the decoder that owns it, with that decoder's own
error type.

## 3. Run identity is caller-supplied and opaque

`run_id` is a string the caller supplies to `put`. It is **never written into
any captured state**, and therefore no existing snapshot schema moves.

There is no run identity anywhere on the execution path today: `run_id` does not
appear in `alphalab/runtime`, `alphalab/backtesting`, `alphalab/replay` or
`alphalab/strategy`. This is a genuinely open choice, which is why it is settled
once, here.

ADR-0017 already answered the same question for the same kind of value.
`dataset_id` is "a caller-provided opaque identity", propagated by the run and
derived by the evidence rather than asserted by the framework, with `None` an
honest absence rather than a default. Run identity is that value's sibling and
gets that value's treatment.

Rejected alternatives are recorded below; the decisive point is that a `run_id`
on `ExecutionPipelineState` would make a run's captured identity depend on where
someone chose to file it.

## 4. `RunStateRef` is an identity, not a location

```text
RunStateRef(run_id: str, sequence: int)      renders "run_id@sequence"
```

Validated at construction: `run_id` non-empty; `run_id` contains no `"@"`;
`sequence >= 0`. The `"@"` rule and its reason are
`alphalab.lifecycle.identity._validate`'s, unchanged — a name containing the
separator parses back to a different reference.

**It is deliberately not shaped like `ArtifactRef`,** and the difference is
substantive rather than stylistic. Surveying every `*Ref` in the tree finds two
families:

| Family | Members | Shape | Means |
| --- | --- | --- | --- |
| External bytes | `ArtifactRef`, `SecretRef` | address plus digest or provider metadata | bytes AlphaLab **never holds**. `SecretRef` says it outright: *an address, not a value* |
| Internal identity | `ModelRef`, `StrategyVersionRef`, `DeploymentRef` | typed, validated, renders `name@version`, round-trips | a typed identity for something AlphaLab **does hold** |

A run-state store holds the bytes, so `RunStateRef` is the second family. It
carries **no URI**. A filesystem path on the reference would put one backend's
addressing into the caller's vocabulary, and would have to be redesigned the
first time a second backend existed — precisely the bridge a frozen v3.0 cannot
afford. Location, digest and byte size stay inside the backend that owns them.

## 5. One real backend, one named double, and no silent fallback

Following `Transport` / `HttpTransport` / `StaticTransport` exactly:

- **`FileRunStateStore`** — the real backend. Standard library only, so
  `dependencies = []` stays true. Writes atomically (temp file, then rename),
  records a digest and verifies it on read.
- **`MemoryRunStateStore`** — a deterministic double, held in memory, which a
  caller constructs **by name** for a test.

`MemoryRunStateStore` is never reached by accident. A `FileRunStateStore` whose
root does not exist, cannot be created, or cannot be written **refuses**, at
construction where that is knowable and at `put` otherwise. It does not degrade,
warn and continue, or hold the payload in memory "for now": a store that reports
success for a run nobody can read back is the failure this ADR exists to remove,
not a convenience.

`StaticTransport`'s docstring states the reason the double must be explicit — it
"makes that same determinism explicit and honest for tests" — and the same
sentence applies here.

## 6. The placeholder store is deprecated; the codec spine is canonical

**Canonical, and untouched:** `serializer`, `decode`, `exceptions`.

**Deprecated in v2.13, removed in v3.0:** `protocol`, `storage`, `state`,
`engine`, `adapter`, `snapshot`, `views`, `validation`, `events` — the nine
modules with zero production importers.

The mechanism is PEP 562 module `__getattr__` on `alphalab/persistence/__init__.py`,
warning only when one of those names is touched. This is the
`alphalab.common.CommonEvent` mechanism, chosen for the reason
`tests/regression/test_deprecation_notices.py` gives: the deprecation notice is
sized to its blast radius. An import-time warning on this package would fire for
every consumer of `serialize`, `decode` and `StateDecodeError` — that is, for
every snapshot module in the repository — to deprecate names those consumers do
not use. "A warning that fires where nobody can act on it is how people learn to
filter DeprecationWarning."

`REMOVAL_RELEASE` gains the corresponding entry.

**Deprecation is not an alias and removes nothing in v2.13.** `MemoryStorage`,
`PersistenceProtocol` and `PersistenceState` keep their current behaviour,
signatures and tests. Nothing is re-exported under a new name, and
`RunStateStore` is not a renamed `PersistenceProtocol`: it has different
addressing, a different unit of storage and a different identifier contract.
D7 is discharged by scheduling the removal, not by performing it.

## 7. The store consumes zero identifiers from the run's stream

A complete `put` / `get` cycle executed inside `id_scope` advances
`current_id_position().draws` by **exactly zero**.

This is the direct answer to the measurement in Context, and it is a contract
rather than an implementation note. It holds because the store mints nothing:
`run_id` is supplied by the caller, `sequence` is an integer the caller chooses
or the store derives from what it already holds, and no system event is emitted.
The store has no event log of its own; a caller who wants one keeps it, outside
the run's scope.

`new_id()` is not called anywhere in the store, and a test asserts the zero.

## 8. Persistence is an explicit caller action between completed steps

The pipeline does not persist. Nothing inside `process_record`,
`process_market_event`, `advance` or `run` calls the store. A caller persists
between `advance` calls, which is the same boundary ADR-0023 decision 7 placed
continuation at, and for the same reason: it is where `id_position` is accurate.

**No append-per-event store API is introduced,** and this is a measurement, not
a preference. On the development machine, one `capture` plus `serialize` of a
1,600-event run costs 1.43 s against 0.40 s for the run itself — 3.6× — and the
payload is 13.4 MB, growing at ~8.4 KB per event. The cost is linear in state
size (4.01× / 4.15× / 4.09× across successive 4× workloads, so there is no
quadratic defect), but capturing per event would make a run quadratic in its own
length: a 1,600-event run would spend roughly nineteen minutes serializing to
produce 0.4 s of trading.

A run that never persists pays nothing, and that is structural rather than
careful: v2.13 adds no code to the per-event path.

## 9. One new schema constant; nothing else moves

```text
+ RUN_STATE_ENVELOPE_SCHEMA = 1
```

A module-local literal rather than `DEFAULT_SCHEMA_VERSION`, for the reason v2.6
gave for the portfolio and v2.9 for the pipeline: that constant also versions
`CommonEvent` and `BaseEvent`, so bumping it would version every event in the
system as a side effect of one subsystem's change.

It versions the store's own wrapper — what the store records *about* a payload —
and nothing inside the payload. It is new, so it has nothing to be compatible
with: no legacy shape, no migration, and no "missing means 1".

Unchanged, and this decision has no reason to move any of them:
`PIPELINE_SNAPSHOT_SCHEMA` (2), `SESSION_SNAPSHOT_SCHEMA` (1),
`BACKTEST_SNAPSHOT_SCHEMA` (1), `ALLOCATION_SNAPSHOT_SCHEMA` (1),
`OMS_SNAPSHOT_SCHEMA` (1), `PORTFOLIO_SNAPSHOT_SCHEMA` (2),
`LIFECYCLE_SNAPSHOT_SCHEMA` (1), `DEFAULT_SCHEMA_VERSION` (1).

`LIFECYCLE_SNAPSHOT_SCHEMA` deserves an explicit note: it is already spoken for.
ADR-0018 defers governance actors so that they land together with ADR-0017's
richer evidence provenance in **one** deliberate bump. A second, unrelated reason
to move it would break that batching, and this decision supplies none.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Run-state capture | `runtime.snapshot`, `runtime.session_snapshot`, `backtesting.snapshot` |
| Run-state restore | the same three modules |
| Continuation | `TradingSession.resume`, `BacktestEngine.resume` |
| Strategy-internal state | the strategy, via `StrategyStateProtocol` (ADR-0025) |
| Identifier position | `ExecutionPipelineState.id_position` (ADR-0022) |
| **Run identity** | **the caller** — opaque, supplied at `put` |
| **Durable persistence** | **`RunStateStore`** — `alphalab.persistence` |
| Backend addressing, digest, byte size | the backend, privately |
| Artifact reference metadata | `model_registry.ArtifactRef` — unchanged, unmoved |
| Artifact bytes | **nobody** — outside AlphaLab, explicitly |
| Venue belief | **not persisted** (ADR-0024) |
| Serialization codec | `persistence.serializer`, `persistence.decode` |

---

# Data model changes

```text
+ RunStateRef              run_id, sequence; renders "run_id@sequence"
+ RunStateStore            put / get / latest / list_runs   (Protocol)
+ FileRunStateStore        the real backend, stdlib only
+ MemoryRunStateStore      the explicitly-named deterministic double
+ RUN_STATE_ENVELOPE_SCHEMA = 1
+ REMOVAL_RELEASE entry for the deprecated persistence store modules

ExecutionPipelineState        # UNCHANGED -- gains no run_id
SessionState, BacktestState   # UNCHANGED
PIPELINE / SESSION / BACKTEST / ALLOCATION / OMS / PORTFOLIO /
LIFECYCLE / DEFAULT schema constants   # UNCHANGED
ArtifactRef                   # UNCHANGED, and not moved
PersistenceProtocol           # UNCHANGED, deprecated, removed in v3.0
MemoryStorage                 # UNCHANGED, deprecated, removed in v3.0
```

---

# Boundary behavior

Write: validate `run_id` and `sequence` → refuse a store whose root is not
writable → write the payload and its digest atomically → return a
`RunStateRef`. Nothing partial is left behind by a refused write.

Read: resolve the reference → refuse an unknown `run_id` or an unwritten
`sequence`, naming both → verify the digest and refuse a mismatch **before any
decode is attempted** → return the payload string.

The store stops there. Turning the payload back into typed values is the owning
snapshot module's `from_primitives`, and continuing the run is `resume` plus
`advance`. Neither is the store's, and the store calls neither.

No path through the store mints an identifier, processes a record, or falls back
to another backend.

---

# Persistence semantics

One coordinated release with one new constant and no movement anywhere else.

A payload written by v2.12 is byte-for-byte what this store holds: the envelope
wraps, and never rewrites, re-encodes or re-orders. An existing
`serialize(capture(state))` string can be placed in a store and read back by the
decoder that has always read it.

Durability is a property of the backend, not of the protocol. `FileRunStateStore`
is durable to the extent the filesystem under it is; `MemoryRunStateStore` is
explicitly not durable and is named so that no caller can believe otherwise.

---

# Testing invariants

1. A `put` / `get` cycle inside `id_scope` advances `current_id_position().draws`
   by **exactly zero**.
2. A run that never persists produces the same identifiers, the same event
   counts and the same final payload as it does on v2.12.
3. `RunStateRef` refuses an empty `run_id`, a `run_id` containing `"@"`, and a
   negative `sequence`; it renders and compares as `run_id@sequence`.
4. `put` then `get` returns the payload byte-for-byte.
5. A `FileRunStateStore` whose root cannot be written **raises**, and no store
   anywhere falls back to memory.
6. A truncated or altered payload fails its digest check and raises before any
   decode is attempted.
7. An unknown `run_id`, and a `sequence` never written, each raise and name what
   was asked for.
8. **The continuation contract, across a process boundary.** A seeded run
   processes N of N+M records, captures, serializes and `put`s; a *separate
   interpreter* `get`s, restores against freshly constructed `RuntimeObjects`,
   resumes and advances the remaining M; the final serialized payload is
   **byte-identical** to that of an uninterrupted N+M run. ADR-0023's Class-1
   comparison and the identifier-list comparison are asserted alongside it, as
   the locators for a byte-identity failure.
9. The store's cost is linear in payload size, and the per-event path is
   unchanged.
10. Touching a deprecated persistence store symbol warns; importing
    `serialize`, `decode` or `StateDecodeError` does not.

---

# Migration and compatibility

No migration framework, consistent with `require_schema_version`'s house rule.

There is no compatibility surface to create: `RUN_STATE_ENVELOPE_SCHEMA` is new,
and every payload the store holds is one an existing decoder already reads at a
version it already declares.

Nothing is removed. The deprecated store modules keep their behaviour and their
tests for the whole of v2.x; v3.0 removes them alongside `kernel`,
`core.events`, `integrations` and `common.CommonEvent`.

---

# Explicit non-goals

- **No FX**, and **no multi-currency valuation**.
- **No `ArtifactStore`**, and **no artifact-byte backend**. Nothing in the tree
  produces artifact bytes: `reporting.export_json`, `export_csv` and
  `export_markdown` all return `str`, and no caller writes them anywhere.
- **No cloud or vendor-specific storage** of any kind.
- **No streaming.**
- **No live venue transport.**
- **No broad runtime rewrite.** The archaeology establishes that the final
  persistence boundary does not require one.
- **No `RunManager`, run facade, or new orchestration object.** `ExecutionPipeline`
  keeps canonical ownership of the state, and the snapshot modules keep canonical
  ownership of its projection.
- **No compatibility aliases** and no re-exports.
- **No migration framework** or version-translation layer.
- **No schema move for convenience** — see decision 9.
- **No change to `ArtifactRef`, and no move of it.**
- **No removal of any deprecated package** in this release.
- **No replay resumability.** `ReplayState` has no snapshot and
  `ReplayBacktest.run` drives a second identifier stream at
  `seed + REPLAY_CURSOR_SEED_OFFSET`; closing that needs a new advance-level API
  and a new constant, and it is additive later rather than a redesign of this
  boundary.
- **No live-object parameter persistence.** A snapshot records
  `sizing_model_type`, `simulator_type`, `instruments_type` and
  `fill_policy_type` as class names, so `EqualWeightSizing(3)` and
  `EqualWeightSizing(30)` type-check identically on restore and size
  differently. Recording parameters would move `PIPELINE_SNAPSHOT_SCHEMA`.
- **No trimming of the payload.** Measured, 75.2% of a pipeline snapshot is
  engine event logs that no engine *decision* reads. Dropping them would
  renegotiate ADR-0023's Class 1, which lists "the order of every event log", and
  that is a contract change with its own ADR, not an optimisation here.

---

# Consequences

Benefits. A run can be stopped, written to disk, and continued in a different
process — which is what "durable run state" has meant since v2.9 and has never
been true. The `capture`/`restore` work of v2.9–v2.10 gains its first production
consumer. The identifier defect measured in Context is closed by contract rather
than by care. The one honest statement about `production.Checkpoint` becomes
available: there is now a real store to point at.

Costs. `alphalab.persistence` carries two store surfaces for the remainder of
v2.x — one canonical, one deprecated — which is the price of not removing
anything in a minor release. `sequence` is a caller-visible integer, so a caller
that persists must decide what its checkpoint numbering means. And a checkpoint
costs 2.9×–3.9× the run that produced it; v2.13 makes persistence real without
making it cheap, and the payload-composition measurement in decision 8 is the
groundwork for whichever later release decides to address that deliberately.

Deferred, and stated so it is not discovered later: replay is not resumable
while backtest and session are.

---

# Alternatives Considered

**Extend `PersistenceProtocol` instead of adding a protocol.** The most
conservative-looking option. Rejected on three counts, each sufficient: it has no
run identity and no sequence, so "the latest state of run X" is inexpressible;
every method threads a `PersistenceState` the *caller* must hold, so the store's
contents are themselves in-memory state someone else keeps alive — which is why
it cannot be durable on its own terms; and every operation emits a system event
and therefore draws an identifier, which decision 7 forbids. Fixing all three
produces a different protocol wearing the old name, which is an alias.

**Persist `run_id` on `ExecutionPipelineState`.** Self-describing: a state would
say which run it belongs to. Rejected. It moves `PIPELINE_SNAPSHOT_SCHEMA` to 3
for a field no engine reads and no decision depends on — a schema move for
storage convenience, which is exactly what ADR-0018 declined to do for
governance actors, and for the reason it gave. It also inverts the relationship:
a run's captured identity would depend on where someone chose to file it.

**Reuse `experiment_tracking.ExperimentRun.run_id`.** A run identity already
exists in the repository. Rejected: it identifies a *research experiment* —
metrics, parameters, a parent run — with a different lifetime, in a standalone
library that is not wired to the execution path. Reusing it would join two
unrelated lifetimes and add an import edge from persistence into experiment
tracking. `start_run` also calls `new_id()`, so it carries the same
stream-consumption defect.

**Derive run identity from `source_id` or `dataset_id`.** No new field, no new
argument. Rejected: both name the *input*, not the run. Two runs over one dataset
are two runs, and ADR-0017 keeps those distinctions apart deliberately.

**Shape `RunStateRef` like `ArtifactRef` — `uri`, `media_type`, `checksum`,
`size_bytes`.** Structurally the same information, and there is a precedent for
the shape. Rejected once the two `*Ref` families in decision 4 were distinguished:
`ArtifactRef` describes bytes AlphaLab never sees, and a store's reference
describes bytes it owns. A `uri` on the reference leaks one backend's addressing
into the caller's vocabulary, and would need redesigning the first time a second
backend appeared.

**Move `ArtifactRef` to a shared package and reuse it.** Rejected: it would make
`alphalab.persistence` or the execution path import the lifecycle packages to
name a saved run, inverting the layering ADR-0023 protected, for one hypothetical
consumer.

**Ship `ArtifactStore` in the same release, since `ArtifactRef` exists.**
Rejected under D4: no producer of artifact bytes exists anywhere in the tree.
`ArtifactRef` existing is a reason to *keep* it unchanged, not a reason to build
the thing it references.

**Typed store methods — `save_session(state)`, `save_backtest(state)`.** More
convenient at the call site, and self-documenting. Rejected under D2: runtime
unification was expected to reshape exactly those two states, and a store naming
them would have to be redesigned by that release. Payload-agnosticism is what
makes this boundary survivable at v3.0 — and v2.14 confirmed it, retiring both
states without touching this module.

**Let the store fall back to memory when a path is unavailable.** Rejected under
D3. It reports success for a run that was not saved, which is worse than the
current honest absence of any store at all.

**Remove the placeholder store now rather than deprecating it.** Cleaner, and it
would leave one store surface instead of two. Rejected: it is a breaking change
in a minor release, and the repository's own precedent — four targets deprecated
in v2.6 for removal in v3.0 — is the established way to retire a public surface
here.

---

# Release impact

Minor-version feature, shipped as **v2.13.0**. Additive: one protocol, one
reference type, two backends, one new schema constant, and one deprecation
notice. No existing schema constant moves, no existing behaviour changes, and
nothing is removed. A run that does not persist is bit-for-bit the v2.12 run.

## What shipped, and what proves it

| Decision | Shipped as | Held by |
| --- | --- | --- |
| 1. One durability owner | `RunStateStore` in `alphalab.persistence` | `test_run_state_store.py` |
| 2. Payload-agnostic | store imports no snapshot, runtime, session, backtesting, portfolio, OMS or strategy module | `test_the_store_imports_no_snapshot_type` |
| 3. Caller-supplied run identity | `put(run_id, sequence, payload)`; no captured state gains a field | `test_the_payload_carries_no_run_identity` |
| 4. Identity, not location | `RunStateRef(run_id, sequence)`, renders `run_id@sequence`, two fields | `test_a_reference_carries_no_storage_detail` |
| 5. One backend, one named double, no fallback | `FileRunStateStore`, `MemoryRunStateStore` | `test_no_refusal_falls_back_to_memory` |
| 6. Placeholder store deprecated, spine canonical | PEP 562 hook over 22 names | `test_deprecation_notices.py` |
| 7. Zero identifiers drawn | no `new_id` in either module | `test_each_operation_on_its_own_draws_zero`, `test_a_refused_operation_also_draws_zero` |
| 8. Explicit boundary persistence | no store symbol in the execution path; four store methods and no `append` | `test_the_execution_path_gained_no_persistence_call`, `test_no_append_per_event_surface_exists` |
| 9. One new constant | `RUN_STATE_ENVELOPE_SCHEMA = 1` | `test_no_existing_snapshot_schema_moved` |

The measurements this decision rests on, taken on the development machine:
a capture plus serialize of a 1,600-event run cost 1.43s against 0.40s for the
run itself and produced 13.4MB, which is why decision 8 forbids an
append-per-event surface; and the encoder's projection check was ~60% of
`serialize`, which the type-lookup removed for a byte-identical payload at
3.70x (698.0ms -> 188.8ms on an 800-event snapshot).

## The seam this leaves for v3.0 — closed in v2.14, as predicted

ADR-0023 decision 1 records that `SessionState` and `BacktestState` are the layer
a future integrated-runtime release is expected to reshape. This boundary is
built so that reshape cannot reach it: the store names neither state, holds only
a `str`, and is addressed by an identity the caller supplies. Runtime unification
moves `SESSION_SNAPSHOT_SCHEMA` and `BACKTEST_SNAPSHOT_SCHEMA` without changing
one line of `run_store.py`. That is the property this release was scoped to
protect, and it is why nothing here is a bridge.

**v2.14 performed that reshape and the prediction held exactly.** ADR-0030
merged both states into one `RunState` behind a single `RUN_SNAPSHOT_SCHEMA = 1`,
retired both old constants, and changed **no code in `alphalab/persistence/`** —
verified as an empty `git diff` over the package and asserted by a regression
test that walks `run_store.py`'s AST and finds no domain type bound or called
there. Decision 2's payload-agnosticism is what made that possible; a store with
`save_session` and `save_backtest` methods would have been redesigned by that
release.
