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
`ExecutionPipelineState` fill / trade / trade-record / snapshot accumulators.

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
asset/strategy indices), `OMSState.active_orders` / `completed_orders`, and
`AllocationState.reservations`.

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

Examples:

- ResearchState
- RuntimeState
- ProductionState
- StudioState
- WorkbenchState
- IntegrationState
- PortfolioState

`alphalab.runtime.ExecutionPipelineState` is a composite: it holds one snapshot of
each subsystem state on the integrated execution path (market, strategy,
allocation, risk, OMS, execution, portfolio, analytics) and is itself a frozen
dataclass replaced wholesale on every step.

`PortfolioState` is the single canonical portfolio state. It owns cash,
positions, the transaction ledger, the event log, and the cumulative
`realized_pnl` and `commission_paid` totals. Unrealized P&L and equity are not
stored: they are derived by `PortfolioValuation.snapshot`, which is a read model
over `PortfolioState`, not a second state object. See
`docs/ARCHITECTURE.md` for the accounting identity these satisfy.

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