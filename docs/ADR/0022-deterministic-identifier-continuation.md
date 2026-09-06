# ADR-0022: Deterministic Identifier Continuation

## Status

Accepted (v2.9.0). Implementation follows in the same release; no code
implements this decision at the time of writing.

Extends ADR-0003, which established deterministic replay, and ADR-0014, whose
round-trip contract this release finally applies to the execution path. Depends
on ADR-0016 only to stay clear of it: instrument identity is derived, not
minted, and nothing here touches it.

---

# Context

Every identifier AlphaLab mints on the execution path — order, execution, fill,
trade, transaction, event — comes from `alphalab.common.ids.new_id`. In a seeded
run that reads a `DeterministicIdSource`, which wraps a `random.Random(seed)`
and draws 128 bits per identifier. The source is installed in a `ContextVar` by
`id_scope(seed)`, which `BacktestEngine.run` and `TradingSession.run` enter for
the duration of a run.

The **seed** has an owner: `BacktestConfig.seed` and `SessionConfig.seed`.

The **position** has none. No state dataclass in the repository holds a field
naming a seed, counter, or identifier source. It exists only inside the live
`Random` object, whose Mersenne Twister position advances on every mint.

The consequence is measurable. A run that stops after N records and continues
for M more must re-enter `id_scope(seed)`, which constructs a *fresh*
`Random(seed)` positioned at zero. Every quantity survives this — measured
across cash, realized P&L, commission, positions, order/fill/trade/report/
snapshot counts, reservations, contributions, `notional_allocated`, risk NAV and
open orders, all identical — but every identifier minted after the restore point
diverges from the uninterrupted run, and the streams **overlap**.

Sweeping every restore point across a workload producing 41 identifiers found up
to 4 genuine collisions: a later `fill_id` minted with a UUID an earlier
`execution_id` already held. The uninterrupted control over the same workload
produced 41 distinct identifiers and zero collisions, so the collisions are
caused by the restart, not by the workload.

Nothing raises. Two distinct facts in one run come to share an identifier, and
every layer accepts it.

---

# Decision drivers

- **D1.** v2.7's identity contract is frozen. A run that reuses its own
  identifiers silently merges two facts in any evidence, attribution or audit
  keyed by them.
- **D2.** Do not increase ambient coupling. `common.ids` already argues that
  threading an id-source parameter through every engine "would put a plumbing
  argument on APIs that have nothing to do with reproducibility". That argument
  still holds.
- **D3.** A persisted value must be inspectable. A payload nobody can read or
  cross-check is not evidence of anything.
- **D4.** `capture(state)` must be a pure function of `state`. Every engine in
  AlphaLab is pure; the snapshot boundary must be too.
- **D5.** Do not build a mechanism the tree does not require.

---

# Decision

## 1. The stream position is `(seed, draws)`, and that is all it is

```text
IdStreamPosition
    seed:  int | None
    draws: int
```

`DeterministicIdSource.__call__` performs exactly one `getrandbits(128)` per
identifier, and that call consumes a fixed number of generator words — verified
by comparing generator states after equal draw counts. There is therefore **one
stream and one cursor**: no per-engine counters, no per-category streams, and
nothing else to record.

## 2. The position lives on `ExecutionPipelineState`

`ExecutionPipeline.process_record` is the canonical step every environment
takes, and the only place that observes draws taken by every engine. Putting the
field on `SessionState` would leave `BacktestEngine.advance` and direct pipeline
callers without it — three call sites each having to remember.

It is a **stored field, refreshed at each step boundary**, not a value read from
the ambient source when a snapshot is taken. Reading ambient at capture time
would make `capture(state)` depend on context rather than on the state,
reintroducing at the persistence boundary the exact ambience that caused the
defect.

## 3. The `ContextVar` remains, and no `new_id()` call site changes

The `ContextVar` is a *delivery mechanism*, and the defect was a *missing state
field*. The evidence is that the seed is delivered by the same ambient mechanism
and has never broken anything.

- `DeterministicIdSource` gains a draw counter, incremented in `__call__`.
- `common.ids` gains a way to read the current position.
- `ExecutionPipeline` refreshes the field at step end.
- The snapshot carries it.

**Zero `new_id()` call sites change.** The eight modules that import it are
untouched.

`process_record` remains ambient-dependent to exactly the degree it already is,
because it mints identifiers. What changes is that after the step the state
carries the position, so `capture(state)` is pure. The ambience is not
increased; the snapshot boundary is made clean.

## 4. Restore reconstructs the source; it does not deserialize one

`restore` returns state and stays pure. A companion constructor builds a
`DeterministicIdSource(seed)` and advances it `draws` draws; the caller installs
it with the existing `use_id_source`. A `resume` entry point composes the two,
mirroring what `run` does with `id_scope`.

Measured fast-forward cost: 79 ns per draw, 7.6–22 draws per market event. A run
of 1,000,000 events resumes in **0.553 s**; 100,000 events in 0.055 s.

## 5. The position is durable state, not a referenced live object

ADR-0014 records a live object as a type name and requires the caller to supply
it back, because a `StrategyProtocol` cannot be reconstructed from data. A
`DeterministicIdSource` **is completely described by two integers**. It is
therefore captured as data and does not appear on the supplied-objects list.

## 6. What determinism is guaranteed relative to

- `(seed, draws)` is a **semantic description of the stream position** — "stream
  seeded S, advanced K draws".
- It **avoids serializing opaque implementation state**: two integers rather
  than a generator's internal word array.
- Deterministic continuation is guaranteed **relative to the AlphaLab
  deterministic-ID algorithm associated with the snapshot's schema version**.
- Changing that algorithm is a **compatibility event that must be explicitly
  versioned and decided**, never a silent change of identifiers.

This ADR does **not** claim that a recorded position yields the same identifiers
across a change of the underlying generator. It would not. `PIPELINE_SNAPSHOT_SCHEMA`
(ADR-0023) identifies the build's ID algorithm, and bumping it is the existing
decision point if that algorithm ever changes. **No PRNG registry, algorithm
abstraction, or generator-version field is introduced.**

## 7. An unseeded run has no continuation guarantee, and says so

With `seed is None` there is no deterministic stream: identifiers come from
`uuid4`. `IdStreamPosition.seed` is `None`, restore promises quantities only,
and continuation remains safe because `uuid4` does not collide. This carve-out
is explicit rather than implied.

---

# Ownership

| Concept | Owner |
| --- | --- |
| The seed | `BacktestConfig.seed`, `SessionConfig.seed` — unchanged |
| The draw counter | `alphalab.common.ids.DeterministicIdSource` |
| The persisted position | `ExecutionPipelineState.id_position` |
| Delivery to `new_id()` | the existing `ContextVar` — unchanged |
| Installing a restored source | the caller, via `use_id_source` |
| Instrument identity | `alphalab.instrument.identity` — **untouched** |

---

# Data model changes

```text
+ IdStreamPosition { seed: int | None, draws: int }
+ ExecutionPipelineState.id_position: IdStreamPosition
+ DeterministicIdSource.draws            # monotonic counter
+ a reader for the current ambient position
+ a constructor: position -> fast-forwarded DeterministicIdSource

new_id()                    # UNCHANGED
use_id_source / id_scope    # UNCHANGED
every new_id() call site    # UNCHANGED
derive_asset_id             # UNCHANGED
```

---

# Boundary behavior

The position is accurate **at step boundaries**, which is the only place a
snapshot is defined. The pipeline is a step function; a capture taken mid-step
is not a thing that exists. Continuation resumes between `process_record` calls
and never inside one.

---

# Persistence semantics

The position is carried in `PipelineSnapshot` (ADR-0023) as two integers. It is
not a separate payload and has no schema constant of its own.

---

# Testing invariants

1. Sweeping every restore point N in [1, total), the union of order, fill,
   trade, execution and transaction identifiers contains no duplicate — **and
   the uninterrupted control is asserted in the same test**, so the assertion
   proves the restart is what changed.
2. A restored run's identifiers equal an uninterrupted run's, position for
   position.
3. `draws` equals the number of identifiers the run has minted.
4. An unseeded run restores, continues, and is asserted for quantities only.
5. Fast-forwarding by `draws` reproduces the stream a source in place would
   have produced.
6. `derive_asset_id` is unaffected: the same declared instrument yields the same
   `asset_id` inside and outside a seeded scope.

---

# Migration and compatibility

No migration. `IdStreamPosition` is new state in a new payload, so there is
nothing to be compatible with. No public signature changes outside the additions
above.

---

# Explicit non-goals

- A PRNG registry, generator abstraction, or pluggable ID algorithm.
- A generator-version field separate from the snapshot schema version.
- Serializing generator internal state.
- Removing or replacing the `ContextVar`.
- Threading an id source through engine signatures.
- Any change to `asset_id` derivation, its four inputs, or the instrument
  namespace.
- Per-category or per-engine identifier streams.

---

# Consequences

Benefits. A restored run can no longer mint an identifier it has already used,
by construction rather than by check. The persisted position is 28 bytes,
human-readable, and cross-checkable against the state that accompanies it. The
existing ambient design is preserved and no call site moves.

Costs. Restore is O(draws) rather than O(1) — 0.553 s at a million events, which
is accepted. The position must be refreshed at every step, which is one ambient
read per step in one place. Determinism is guaranteed relative to a specific ID
algorithm, and changing that algorithm becomes a versioned decision rather than
a free one.

---

# Alternatives Considered

**Serialize the generator's internal state.** The measured alternative: a
7,369-byte payload of 625 integers, restoring in constant time rather than
0.553 s at a million events. Rejected despite the better cost profile. The
payload is opaque — nobody can read it, and nothing can cross-check it against
the state it accompanies — and it pins the persisted format to one generator
implementation permanently. "Two integers, verifiable" beats "625 integers,
faster" when the faster one is already fast enough.

**Content-addressed identifiers**, derived from run id plus a counter. Rejected:
it changes every identifier every run has ever produced, breaking reproducibility
of existing recorded runs, and it is a much larger change than the defect.

**A per-run monotonic counter feeding a separate derivation.** Rejected for the
same reason, and it would introduce a second identity-minting mechanism
alongside the existing one.

**Forbid restore for seeded runs.** Rejected: it converts the defect into a
missing capability and blocks the release's entire purpose.

**Read the position from the ambient source at capture time rather than storing
it.** Rejected: it makes `capture(state)` depend on context instead of on state,
which is the ambience that caused the defect, relocated to the persistence
boundary.

---

# Release impact

Minor-version feature. Additive: one new field on one state, one counter on one
class, two new helpers. No existing signature changes, no persisted format
migration, and no movement in the instrument identity contract.
