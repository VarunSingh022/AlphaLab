# ADR-0023: The Run-State Snapshot Envelope

## Status

Accepted (v2.9.0). Implementation follows in the same release; no code
implements this decision at the time of writing.

Applies ADR-0014's round-trip contract — "restore reconstructs semantics, not
structure", and "live objects are referenced, not reconstructed" — to the
execution path, which is the one place it was never applied. Depends on
ADR-0022 for the identifier position the envelope carries, and on **ADR-0019**
for the one-account-currency invariant that restore must re-assert. Gives
`AllocationState` the snapshot ADR-0021's ledgers need in order to survive a
round trip.

---

# Context

Three states in AlphaLab have a `capture`/`restore` pair with a field-coverage
guard: `PortfolioState`, `LifecycleState` and `OMSState`. Nothing captures the
composite state the execution path actually threads.

Measured field by field on a real post-run `ExecutionPipelineState`, **11 of 15
fields serialize as they stand**. The failures are narrower than "live objects
prevent serialization" suggests:

- `config` fails on `sizing_model` and on `simulator`;
- `strategy` fails on `StrategyState.instance`.

Everything else — market, allocation, risk, OMS, execution, portfolio,
analytics, market prices, and all four append-only logs — already encodes.

So the obstacle is not the state model. It is that four values on the state are
not data, and no contract said what to do about them. ADR-0014 already answered
that question for `ModelVersion.model`: record what it was, and require the
caller to supply it back. That answer has simply never been applied here.

---

# Decision drivers

- **D1.** Reuse the accepted contract. ADR-0014 settled how a live object
  crosses a snapshot boundary; this release applies it rather than inventing a
  second rule.
- **D2.** Confine future schema churn to the layer expected to move.
- **D3.** Do not create a schema constant without a consumer.
- **D4.** A second path into the same state must enforce the same invariants as
  the first.
- **D5.** Never substitute. ADR-0014's `_require_model` raises on a missing
  object and on a type mismatch, and never fills in `None`.
- **D6.** Do not persist a belief AlphaLab cannot verify (ADR-0024).

---

# Decision

## 1. Two envelopes, not one

```text
RunSnapshot                      # SESSION_SNAPSHOT_SCHEMA = 1
  schema_version
  pipeline: PipelineSnapshot
  processed, current_timestamp
  session:  last_record_timestamp, source_id, skipped[]
  backtest: steps[]

PipelineSnapshot                 # PIPELINE_SNAPSHOT_SCHEMA = 1
  schema_version
  id_position   { seed, draws }              # ADR-0022, data not a reference
  config        { account, starting_cash, budget, allocation_constraints,
                  risk_limits, venue, currency, routing,
                  sizing_model_type, simulator_type, instruments_ref }
  strategy      [{ strategy_id, status, config, subscriptions,
                   last_error, instance_type }]
  market        { latest_quotes, latest_books, latest_ticks,
                  latest_bars, history[], events[] }
  allocation    -> AllocationSnapshot        # ALLOCATION_SNAPSHOT_SCHEMA = 1
  risk          { active_limits, margin, exposure, cash, buying_power,
                  peak_nav, current_nav, daily_loss, history[], events[] }
  oms           -> OMSSnapshot               # OMS_SNAPSHOT_SCHEMA = 1
  execution     { reports{}, history[], events[] }
  portfolio     -> PortfolioSnapshot         # schema 2, UNCHANGED
  analytics     { reports[], events[] }
  market_prices {}
  fills[], trades[], trade_records[], portfolio_snapshots[]
  unpriced_assets {}
```

The split is deliberate. `ExecutionPipelineState` is the stable core and is not
expected to change shape. `SessionState` and `BacktestState` are the layer a
future integrated-runtime release is expected to reshape. A single envelope
would therefore need a version bump one release later — adjacent-release schema
churn, which is precisely what a version field is supposed to prevent rather
than schedule. Two envelopes confine the churn to the layer that will actually
move.

## 2. Which states get their own schema constant, and which do not

| State | Treatment | Constant |
| --- | --- | --- |
| Portfolio | existing snapshot, unchanged | `PORTFOLIO_SNAPSHOT_SCHEMA` = 2 |
| Lifecycle | separate lifetime, untouched | `LIFECYCLE_SNAPSHOT_SCHEMA` = 1 |
| OMS | existing snapshot, now versioned | `OMS_SNAPSHOT_SCHEMA` = 1 |
| Allocation | **new snapshot** | `ALLOCATION_SNAPSHOT_SCHEMA` = 1 |
| Pipeline | **new envelope** | `PIPELINE_SNAPSHOT_SCHEMA` = 1 |
| Run / session | **new envelope** | `SESSION_SNAPSHOT_SCHEMA` = 1 |
| Market, Risk, Execution, Analytics, Strategy | inline in the pipeline envelope | **none** |

Allocation is first-class because ADR-0021's ledgers are load-bearing:
reservations and contributions decide whether a restored run's committed capital
and attribution are correct, and they have non-trivial reconstruction.

The five inline states get no constant because they have no standalone consumer.
Five more public snapshot modules with five more version constants would be five
future churn points for nobody.

## 3. Live objects are referenced and supplied back; nothing is substituted

Recorded as a type name, required from the caller on restore:

- `config.sizing_model`, `config.simulator`, `config.instruments` (when non-null)
- each `StrategyState.instance`
- `fill_policy`, on the run envelope

Restore **raises** when a required object is missing, and **raises** when a
supplied object's type does not match the recorded one. It never substitutes
`None`. These are ADR-0014's two checks, applied to the execution path.

The identifier position is explicitly *not* on this list: it is fully described
by two integers, so it is captured as data (ADR-0022 decision 5).

## 4. Restore re-runs every construction-time validation

`_require_one_account_currency` is called at exactly one site — inside
`ExecutionPipeline.initialize`. Restore does not go through `initialize`.

Left alone, a snapshot could reconstruct a pipeline state that `initialize`
would have refused, and ADR-0019's guarantee would hold for started runs but not
for restored ones. That is the class of defect that makes a round trip
untrustworthy: not a value decoded wrongly, but an invariant that stops being
checked on the second path into the same state.

**`restore` re-runs every construction-time validation `initialize` enforces,
before returning a state.** Today that is the one-account-currency check; the
rule is general, so a validation added to `initialize` later is covered without
amending this ADR.

ADR-0020's mixed-currency refusal needs no equivalent: it validates at each call
rather than at construction, so a restored state is checked the first time it is
valued.

## 5. OMS legacy compatibility — a bounded exact-shape rule

`OMSSnapshot` is the only round-trip snapshot without a version field, and
`alphalab.oms` exports `capture`, `restore` and `from_primitives` while its
module docstring teaches the JSON round trip as a public recipe. There is
therefore a documented public history to be compatible with.

**Newly written payloads MUST carry `schema_version = 1`.** `capture` never
emits an unversioned payload.

**A missing `schema_version` is accepted only when the payload's key set exactly
equals the documented `LEGACY_UNVERSIONED_V0` shape:**

```text
orders, active_orders, completed_orders, history, events
```

Refused, in every other case:

- a partial match — the legacy shape with a key missing;
- an over-match — the legacy shape with an extra key;
- any other unversioned shape;
- a malformed payload;
- `schema_version >= 2`.

This is **not** "missing means version 1", **not** a generic default, and **not**
acceptance of arbitrary unversioned payloads. It is one named, frozen historical
shape, recognised by total structural match, with every field decoder still
validating every value.

The rule applies to **standalone** OMS payload decoding only. An OMS snapshot
nested inside `PipelineSnapshot` was written by v2.9 or later and always carries
its version, so the envelope never relies on shape inference.

Why compatibility rather than a break: the portfolio precedent refused schema 1
for a substantive reason — a v1 payload does not record `Position.opened_at`, and
no honest value could be invented for it. The OMS case has **no missing data at
all**; a pre-v2.9 payload contains every field the decoder reads. Refusing it
would discard a payload that can be read perfectly, for no informational reason.
ADR-0014's stated purpose is that "the first schema change is a decision rather
than a silent misread"; this is that decision, made explicitly and tested.

## 6. What is not persisted

`BrokerState` and `ExternalOrderMap` are excluded — see ADR-0024.

## 7. Continuation happens only at completed step boundaries

`restore` returns state. Continuation resumes between `process_record` calls and
never inside one, which is also where ADR-0022's position is accurate.

## 8. The equivalence contract is conditional, and says so

For a **seeded** run, given the **same records in the same order**, the **same
supplied runtime objects**, and **strategy-internal state restored by the
caller**: capture → restore → continue is equivalent to uninterrupted execution
across the Class-1 state below and every deterministic identifier.

| Class | Values |
| --- | --- |
| **1 — must be identical** | positions, cash, reserved, realized and unrealized P&L, commission, transaction ledger; reservations, contributions, `notional_allocated`, request history; every order and status, filled and remaining quantity, average fill price, active/completed sets; execution reports keyed by id; risk cash, buying power, peak and current NAV, daily loss, margin, exposure, decision history; analytics reports and the snapshot series; strategy status, config, subscriptions, `last_error`; market latest indexes; **every order, fill, trade, execution and transaction identifier** and `id_position.draws`; trade records and their contributions; processed count, timestamps, skipped records, backtest steps; and the order of every event log |
| **2 — may differ, semantically equivalent** | container lineage only: `PersistentMap` version chains, `PersistentSet` branch history, `AppendOnlyLog` buffer sharing — ADR-0014's existing position. Nothing observable through `==` is in this class. |
| **3 — intentionally regenerated** | identifiers in an unseeded run (`uuid4`, cannot match, cannot collide); staleness decisions in a real-time session, which depend on the wall clock passed to `advance` |

**The fourth precondition is a real limitation.** `StrategyProtocol` declares ten
hooks and **no state-serialization hook**. A strategy holding a rolling window or
counter in Python attributes holds state AlphaLab cannot capture and cannot
restore. v2.9 does not pretend otherwise: equivalence holds for strategies whose
intents are a function of `(context, event)` plus whatever state the caller
restores itself.

A `StrategyStateProtocol` is **not** v2.9 scope. It is deferred, together with
`StrategyContext` completion, and is recorded as a known internal item that must
be closed before v3.0.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Composite durable state | `ExecutionPipelineState` |
| Run bookkeeping | `SessionState`, `BacktestState` |
| Reservation and contribution ledgers | `AllocationSnapshot` (new) |
| Order book projection | `alphalab.oms.snapshot` — now versioned |
| Portfolio projection | `alphalab.portfolio.snapshot` — unchanged |
| Identifier position | `ExecutionPipelineState.id_position` (ADR-0022) |
| Supplied live objects | the caller, checked on restore |
| Venue belief | **not persisted** (ADR-0024) |

---

# Data model changes

```text
+ PipelineSnapshot         capture / restore / from_primitives
+ RunSnapshot              capture / restore / from_primitives
+ AllocationSnapshot       capture / restore / from_primitives
+ OMS_SNAPSHOT_SCHEMA        = 1     # new field on an existing payload
+ ALLOCATION_SNAPSHOT_SCHEMA = 1
+ PIPELINE_SNAPSHOT_SCHEMA   = 1
+ SESSION_SNAPSHOT_SCHEMA    = 1
+ LEGACY_UNVERSIONED_V0 key set for standalone OMS decoding
+ a resume entry point composing restore with a fast-forwarded id source

PORTFOLIO_SNAPSHOT_SCHEMA    # UNCHANGED (2)
LIFECYCLE_SNAPSHOT_SCHEMA    # UNCHANGED (1)
PersistenceProtocol          # UNCHANGED
```

---

# Boundary behavior

Refuse an unknown envelope version → refuse an unknown pipeline version →
require and type-check every supplied live object → rebuild each subsystem
through its own `restore` where one exists → **re-assert construction-time
invariants** → return state. Nothing partial is returned by a refused restore.

---

# Persistence semantics

One coordinated schema release. Four constants are introduced or added; two
existing constants do not move. Only OMS carries a legacy path, because it is
the only payload with a documented public history; every other constant is new
in v2.9 and has nothing to be compatible with.

The container migration that makes `alphalab.persistence` linear is a
prerequisite of this decision, not part of it: it applies the v2.1/v2.2
`AppendOnlyLog` / `PersistentMap` / `PersistentSet` pattern to the one package
that missed it, changes four field types, and leaves `PersistenceProtocol` and
every method signature untouched. It needs no ADR of its own.

---

# Testing invariants

1. `restore(capture(s), objects) == s` for pipeline, run and allocation state.
2. Adding a field to a captured state fails a test until its snapshot names it
   or declares why not — the existing field-coverage guard, extended.
3. A missing supplied object raises, naming it; a wrong type raises, naming
   both.
4. A snapshot whose `config.currency` and `account.base_currency` disagree is
   refused by `restore` with the same error `initialize` raises.
5. OMS compatibility, six fixtures: the exact legacy five-key payload reads; the
   legacy shape with a key missing is refused; with an extra key is refused;
   `schema_version = 2` is refused; `schema_version = 1` reads; and `capture`
   never emits an unversioned payload.
6. Reservations and contributions survive a round trip, and a restored working
   order can still reach a terminal state and retire both.
7. Class-1 values compare equal between an uninterrupted run and a restored one,
   under the stated preconditions.
8. An unseeded run restores, continues, and is asserted for quantities only.
9. A strategy with unrestored internal state is **expected** to diverge, and a
   test names that expectation rather than leaving it implicit.

---

# Migration and compatibility

No migration framework, consistent with `require_schema_version`'s house rule.
The only compatibility surface is the OMS legacy shape in decision 5, which is
deliberate, bounded and tested. Every other payload is new.

---

# Explicit non-goals

- A migration framework or version-translation layer.
- Schema constants for Market, Risk, Execution, Analytics or Strategy.
- Persisting `BrokerState` or `ExternalOrderMap` (ADR-0024).
- A `StrategyStateProtocol`, or any capture of strategy-internal state.
- `StrategyContext` completion.
- Unifying `SessionState` and `BacktestState`, or removing the parallel runtime
  mechanism.
- Moving `PORTFOLIO_SNAPSHOT_SCHEMA` or `LIFECYCLE_SNAPSHOT_SCHEMA`.
- Capturing `LifecycleState` into this envelope; its lifetime is a deployment,
  not a run.

---

# Consequences

Benefits. A run can stop and continue without losing state or duplicating
identifiers. ADR-0014's contract finally covers the execution path. ADR-0021's
ledgers become durable. The v2.8 currency invariant holds on both paths into a
pipeline state, not just the first. Two envelopes keep the next release's
expected reshape from bumping the stable core.

Costs. Six schema constants now exist where two did. The OMS legacy branch is a
permanent compatibility commitment, deliberately taken. The equivalence contract
is conditional, and one of its preconditions — restored strategy-internal state —
is the caller's responsibility with no supporting protocol until a later
release.

---

# Alternatives Considered

**One monolithic envelope.** Simpler to write and to version. Rejected: the
session layer is expected to be reshaped by the integrated-runtime work, which
would bump the whole envelope one release later and version the stable pipeline
core as a side effect — the same mistake ADR-0015 avoided when it declined to
bump a shared constant for one subsystem's change.

**A snapshot module per subsystem.** Most consistent with the portfolio/OMS/
lifecycle precedent. Rejected for the five states with no standalone consumer:
it would create five public modules and five version constants that nothing
reads, each an independent future churn point.

**Event-sourced replay instead of a snapshot.** Rebuild state by replaying its
event history rather than capturing it. Rejected: the histories are already in
the state, so replay would still need the state to start from; it would make
restore cost proportional to run length rather than to state size; and it does
not answer the live-object question at all.

**Refuse all unversioned OMS payloads (the strict reading of precedent).**
Considered seriously and rejected — see decision 5. The portfolio precedent
refused because it *could not read* a v1 payload honestly; the OMS decoder can
read a pre-v2.9 payload perfectly. Applying the letter of that precedent against
its reason would break a documented public recipe for no informational gain.

**Accept any unversioned payload as version 1.** Rejected. It contradicts
`require`'s stated rule that "a missing field is never filled in with a
default", and it would let the decoder read payloads the encoder never writes —
the second dialect `as_named_enum` warns against.

---

# Release impact

Minor-version feature. Additive across three new snapshot surfaces and four new
schema constants, with one bounded legacy-compatibility rule on an existing
public decoder. No existing schema constant moves; `PersistenceProtocol` is
unchanged.
