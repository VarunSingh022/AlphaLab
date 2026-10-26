# AlphaLab State Model

## Overview

Every AlphaLab subsystem owns an immutable state object representing the complete state of that subsystem.

State objects are implemented as frozen dataclasses and are never modified in place.

---

# Philosophy

Instead of mutating existing state, every operation returns a new state instance.

```
Previous State

↓

Operation

↓

New State
```

This approach simplifies testing, replay, debugging, and reasoning about system behavior.

---

# State Characteristics

Every state object should be:

- Immutable
- Typed
- Serializable
- Deterministic
- Self-contained

---

# Implementation

State objects typically use:

```python
@dataclass(frozen=True, slots=True)
```

This provides immutability and efficient memory usage.

---

# Append-only histories

Most state objects carry append-only histories -- `events`, `history`, the
portfolio's transaction ledger. As of v2.1 these are
`alphalab.common.AppendOnlyLog`, not `tuple`.

An `AppendOnlyLog` is still an immutable `Sequence` with value semantics:
`len()`, indexing, slicing, iteration, `in`, `reversed()` and equality against a
tuple or list all behave as before, and `append()` / `extend()` return a *new*
log rather than mutating the one they were called on.

What changed is the cost. Growing a tuple with `(*state.events, event)` rebuilds
the whole tuple, so N transitions copy O(N^2) elements. An `AppendOnlyLog` is a
view `(buffer, length)` over a shared backing list: appending to the newest
version pushes onto the shared buffer and returns a longer view, which is O(1)
amortized. Appending to an *older* version would collide with entries past its
end, so that case copies the prefix into a fresh buffer -- copy on branch. Older
views only ever read `buffer[:length]`, so they never observe later appends.

The backing buffer is only ever appended to and is not safe to grow from several
threads at once; AlphaLab's engines are single-threaded and deterministic.

States using `AppendOnlyLog`: `RiskState`, `MarketState`, `ExecutionState`,
`OMSState`, `AllocationState`, `PortfolioState`, `TransactionLedger`, and the
`ExecutionPipelineState` fill / trade / trade-record / snapshot accumulators
(v2.1); `StrategyStudioState`, `Project` and `WorkbenchState` (v2.16; they left
with `studio` and `workbench` in v3.11); and
`scheduler`, `feature_store`, `distributed`, `plugins`, `reporting`, `optimizer`,
`data`, `cluster_scheduler` and `portfolio_optimizer` (v2.17; `plugins` and
`optimizer` were removed in v3.12).

`strategy` and `analytics` histories grow per lifecycle transition or per compiled
report rather than per market event, and are left as tuples deliberately.

Serialization goes through `alphalab.common.dataclass_to_dict`, which converts an
`AppendOnlyLog` exactly as the tuple it replaced. Use it (or
`alphalab.persistence.serialize`) rather than `dataclasses.asdict` directly:
`asdict` cannot be extended, so it deep-copies the log as an opaque object.

## Persistent keyed state (v2.2)

`alphalab.common.PersistentMap` and `PersistentSet` are the same idea applied to
keyed containers, and exist for the same reason: a `dict`/`frozenset` field
rebuilt on every write makes a run of N transitions copy O(N²) entries. A map is
a view `(store, version, size)` over shared append-only storage that keeps, per
key, the chain of `(version, value)` writes to it. A view reads the newest entry
at or before its own version, so a later write is invisible to it; writing to
the newest view appends one entry (O(1) amortized) and writing to an older view
copies -- copy on branch, exactly as `AppendOnlyLog` does.

Iteration is in first-insertion order, so a state holding one serializes
deterministically.

States using them: `OMSState.orders` (the `OrderBook`'s order index and its
asset/strategy indices), `OMSState.active_orders` / `completed_orders`,
`ExecutionState.reports`, and `AllocationState.reservations`.

## Serializable projections (v2.2)

A state whose in-memory shape has no JSON form declares one by defining
`__serializable__()`, which `dataclass_to_dict` honours before its dataclass
branch. `OMSState` uses it: `OrderBook` keys orders by the `OrderId` dataclass,
which JSON cannot use as an object key, so the state projects to
`alphalab.oms.snapshot.OMSSnapshot` -- orders as an array in submission order,
derived indices omitted and rebuilt on restore, and every event tagged with its
`event_type` so the log reads back as typed events.

`capture` / `restore` are inverses in memory, and
`restore(from_primitives(deserialize(serialize(state)))) == state` across JSON.

A value *without* such a projection is still rejected by the encoder rather than
stringified: the mechanism is an explicit declaration, not a fallback.

---

# Ownership

Each package owns exactly one primary state object.

| Package | Primary state |
| --- | --- |
| `market` | `MarketState` |
| `strategy` | `RuntimeState` — registered strategy instances and their stage |
| `allocation` | `AllocationState` — reservations and contributions |
| `risk` | `RiskState` |
| `oms` | `OMSState` |
| `execution` | `ExecutionState` |
| `portfolio` | `PortfolioState` |
| `analytics` | `AnalyticsState` |
| `instrument` | `InstrumentRegistry` |
| `broker` | `BrokerState` |
| `runtime` | `ExecutionPipelineState` (the step) and `RunState` (the run) |
| `lifecycle` | `LifecycleState` — the four registries plus the evidence store |
| `research` | `ResearchState` |
| `replay` | `ReplayState` |
| `data` | `UniversalDataState` — datasets keyed by version, the catalogue, quality reports, schemas, metadata, `lineage` (derived version → parent) and the event log. A version is never overwritten: cleaning and resampling add a new entry and record the parent (ADR-0036). |

**One name is reused deliberately.** `RuntimeState` in
`alphalab.strategy.state` holds strategy instances and is not a runtime-package
state; the orphan `RuntimeState` that once was one was removed in v2.17. Until
v3.13 `LifecycleState` was a second: the whole lifecycle registry in
`alphalab.lifecycle.state`, and an enum naming the stages of a strategy
*instance running inside a session* in `alphalab.strategy.state`. The enum is
`StrategyStatus` now (ledger API-001) — a deployed strategy version is started
and stopped many times without its stage changing.

`alphalab.runtime.ExecutionPipelineState` is a composite: it holds one snapshot of
each subsystem state on the integrated execution path (market, strategy,
allocation, risk, OMS, execution, portfolio, analytics) plus the market price map,
the accumulators, the unpriced-asset record and the identifier position, and is
itself a frozen dataclass replaced wholesale on every step.
`alphalab.runtime.run.RunState` sits above it and holds the run: the cursor, what
was skipped, what each record produced, and the source identity.

`PortfolioState` is the single canonical portfolio state. It owns cash,
positions, the transaction ledger, the event log, and the cumulative
`realized_pnl` and `commission_paid` totals — which are `CurrencyAmounts` as of
v2.17, so a EUR fill accrues EUR P&L and nothing is summed across two currencies.
Unrealized P&L and equity are not stored: they are derived by
`PortfolioValuation.snapshot`, which is a read model over `PortfolioState`, not a
second state object, and which names one reporting currency and records every
rate it converted through. `tests/regression/test_shared_names_stay_distinct.py`
asserts that no second portfolio model exists anywhere in the package. See
`ARCHITECTURE.md` for the accounting identity these satisfy.

---

# Durability: snapshots and schemas

A state is durable when it has **one** snapshot owner, **one** schema constant,
and a typed decoder that refuses what it does not understand. Eleven do (v4.1):

| State | Snapshot module | Schema constant | Value |
| --- | --- | --- | --- |
| `OMSState` | `oms.snapshot` | `OMS_SNAPSHOT_SCHEMA` | 2 |
| `PortfolioState` | `portfolio.snapshot` | `PORTFOLIO_SNAPSHOT_SCHEMA` | 5 |
| `LifecycleState` | `lifecycle.snapshot` | `LIFECYCLE_SNAPSHOT_SCHEMA` | 2 |
| `AllocationState` | `allocation.snapshot` | `ALLOCATION_SNAPSHOT_SCHEMA` | 3 |
| `ExecutionPipelineState` | `runtime.snapshot` | `PIPELINE_SNAPSHOT_SCHEMA` | 8 |
| `RunState` | `runtime.run_snapshot` | `RUN_SNAPSHOT_SCHEMA` | 5 |
| `InstrumentRegistry` | `instrument.snapshot` | `INSTRUMENT_SNAPSHOT_SCHEMA` | 4 |
| `BrokerState` | `broker.snapshot` | `BROKER_SNAPSHOT_SCHEMA` | 2 |
| `LiveRunState` | `runtime.live_snapshot` | `LIVE_SNAPSHOT_SCHEMA` | 2 |
| `FxFeedState` | `portfolio.fx_feed` | `FX_FEED_SNAPSHOT_SCHEMA` | 1 |
| `PausedReplay` | `backtesting.replay_snapshot` | `REPLAY_SNAPSHOT_SCHEMA` | 2 |

`InstrumentRegistry` schema 4 (v4.1.1) carries each instrument's last trading
instant (FUT-012) and its contract terms (FUT-013) -- `OptionTerms` or
`FutureTerms`, enum members by name and amounts as text -- read back through
the record's own validation, so a payload stating terms a contract cannot have
is refused. Neither enters the identity key: every `asset_id` is what it was.

`PausedReplay` (v4.1, ledger FUT-001) is a replay stopped between two records:
its envelope nests the run snapshot unchanged and adds the replay cursor and the
position of the cursor's own identifier stream. Its `restore` also takes the
dataset back -- the replay's input, recorded by id, length, the ids at the
boundary and a digest of every record, and refused when it differs. Since
v4.1.1 (schema 2) it also records its schedule's digest and, for a replay given
an execution plan (ledger FUT-009), the plan's algorithm book, written by
`backtesting.algorithmic.capture_book` (`ALGORITHM_BOOK_SCHEMA = 1`): every
parent's algorithm with its children, and every child fill. The plan is taken
back by `restore` and held to the book by its `plan_id`, and each algorithm run
is rebuilt and checked against its recorded identity. The book is driver state,
not part of `RunState`, so no run's record or `result_id` moved. For a replay
given a queue model (ledger FUT-011) it records the queue book on the same
terms, written by `backtesting.queueing.capture_queue` (`QUEUE_BOOK_SCHEMA = 1`):
the model's type and configuration, and each resting limit order's limit and
the quantity ahead of it. The model is taken back by `restore` and refused when
its type or configuration differs.

Three envelopes version only what they add around a payload:
`RUN_STATE_ENVELOPE_SCHEMA` (the run-state store, 1), `CHECKPOINT_SCHEMA` (an
incremental checkpoint, 2) and `EVIDENCE_SCHEMA` (the evidence store, 1).
`tests/regression/test_documented_schemas_are_current.py` holds this table to
the constants.

Each module exposes the same three functions — `capture(state)` →
serializable projection, `from_primitives(payload)` → typed snapshot,
`restore(snapshot)` → typed state — and the contract is
`restore(capture(s)) == s`.

## Rules the schemas follow

- **Every constant is a module-local literal.** None aliases
  `DEFAULT_SCHEMA_VERSION`, because that constant also versions `BaseEvent`:
  bumping it would version every event in the system as a side effect of one
  subsystem's change. Four regression tests pin the de-aliasing.
- **Every version a release wrote is read, or refused with its reason.** Since
  v3.10 each subsystem declares a `SchemaHistory` (`alphalab.persistence.upgrade`)
  with one `SchemaStep` per version it ever wrote: a pure upgrade to the next
  version, or the reason no honest upgrade exists. An upgrade supplies a value
  only when it is what the older payload already meant, and a version no
  release wrote is refused, naming the versions this build reads. The payloads
  v3.9.0, v3.11.0, v3.12.0 and v3.13.0 wrote are frozen under
  `tests/fixtures/snapshots/` and read by every build, and the release
  certificate's REP-3 reads every release frozen there; v4.0 moved the pipeline
  from 7 to 8 and the run from 4 to 5, and reads a payload v3.13 wrote as one
  that did not record how its live objects were configured (ledger PER-008,
  `tests/regression/test_schema_upgrades_v3_13.py`).
- **A plain enum's class name is part of the format.** A `StrEnum` member is
  written as its value (`"market"`); a plain `Enum` member as
  `ClassName.MEMBER` (`"StrategyStatus.RUNNING"`), and the decoder reads only
  that form. Renaming such a class, or a member, is therefore a schema change
  and needs an upgrade step: v3.13 renamed the strategy runtime's
  `LifecycleState` to `StrategyStatus` and moved the pipeline from 6 to 7 to
  rewrite it (ledger PER-004). `tests/regression/test_persisted_enum_names.py`
  lists every persisted enum's class and member names, so a rename without a
  step fails there before it fails a reader.
- **Envelopes nest rather than merge.** The live envelope carries a run snapshot
  and a broker snapshot, each versioned by its own constant, so adding venue
  durability moved no schema a backtest writes (ADR-0023, ADR-0030).
- **Live objects are referenced, not reconstructed.** A strategy instance, a
  simulator, a sizing model, a fill policy and an instrument registry are recorded
  *by type*; `restore` requires the caller to supply them back and raises on a
  missing or mistyped one, never substituting. Since v4.0 the sizing model, the
  simulator and the fill policy are also recorded by **configuration** --
  `alphalab.runtime.assumptions.describe`, their parameters recursively, a
  mapping or a set by its sorted contents, and no memory address -- and
  `restore` refuses one configured differently, naming
  both; the description is part of a manifest's `configuration_id` (ADR-0049,
  ledger PER-008, PER-009). A payload v3.13 or earlier wrote checks by type alone, as it
  always did. The instrument registry is compared by nothing (ADR-0027), and a
  strategy's durable state is what it declares through `StrategyStateProtocol`;
  the value its `configure` received is persisted as JSON reads it (ledger
  LIM-004), which v4.1 states as a contract, held row by row through the real
  restore path by `tests/regression/test_strategy_configuration_contract.py`:

  | A configured value of type | Reads back after a restore as |
  | --- | --- |
  | `None`, `bool`, `int`, `str`, a finite `float`, and lists and `str`-keyed mappings of these, at any depth | itself, exactly (the sign of a zero included) |
  | `Decimal` | its exact text -- `Decimal("10.00")` as `"10.00"`, `Decimal("1E+3")` as `"1E+3"` -- which `Decimal(text)` rebuilds exactly |
  | `tuple` | a `list` |
  | a mapping with a non-`str` key | a mapping with the key's JSON text (`1` as `"1"`, `True` as `"true"`) |
  | `StrEnum` member / plain `Enum` member | its value / `"ClassName.MEMBER"` |
  | `UUID` | its text |
  | dataclass | a mapping of its fields |
  | `set`, `frozenset`, a `NaN` or infinite `float`, any other object | refused at capture: `SerializationError` naming the type |

  `restore(capture(s)) == s` therefore holds for a strategy configured with
  values of the first row, and the run's identities are the same before and
  after a restore either way. Since the v4.2.0 candidate (ledger FUT-002) it
  holds for every row but the last when the strategy's class declares the
  configuration's type (`configuration_type`; `alphalab.strategy.configuration`):
  `configure` refuses a value not of that type, and restore reads the recorded
  form back as it -- the payload is the one this table describes, so no schema
  or identity moves (`tests/regression/test_typed_strategy_configuration.py`).
- **Derived indexes are rebuilt, not stored.** The order book's asset and strategy
  indexes and the instrument registry's provider index are reconstructed by
  replaying the records. One fact, one home.
- **Identity is re-derived, never asserted.** `restore` recomputes every
  `asset_id` from its declaration, so a payload cannot claim an identity.

## Where a payload goes

`alphalab.persistence.RunStateStore` — four methods over
`(run_id, sequence) → payload`. It is payload-agnostic: it moves a `str`, imports
no snapshot type and decodes nothing, which is what keeps it correct across
schema changes. `FileRunStateStore` writes atomically and verifies a SHA-256
digest **before any decoder runs**; `MemoryRunStateStore` is an explicitly named
double that nothing selects automatically. A seeded run can stop in one process
and finish in another, producing a byte-identical payload (ADR-0029).

---

# State Transitions

Every operation follows the same pattern:

```
Input State

↓

Validation

↓

Business Logic

↓

New State

↓

Events
```

The original state remains unchanged.

---

# Metadata

State objects may include metadata for extensibility, but metadata should never alter deterministic behavior.

---

# Relationship to Events

State represents the current snapshot of the system.

Events describe how the system reached that snapshot.

Both concepts complement each other but have distinct responsibilities.

---

# Benefits

- Deterministic execution
- Easier testing
- Thread safety
- Predictable replay
- Reduced side effects