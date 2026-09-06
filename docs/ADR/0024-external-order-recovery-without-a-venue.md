# ADR-0024: External Order Recovery Without a Venue

## Status

Accepted (v2.9.0). Implementation follows in the same release; no code
implements this decision at the time of writing.

Completes ADR-0021, which unified the meaning of a terminal transition for
simulated routing and explicitly left the externally routed path without one.
Depends on ADR-0023 for the snapshot that makes a working order durable, and on
ADR-0012 for the broker boundary this decision declines to cross.

---

# Context

Under `ExecutionRouting.EXTERNAL` an accepted order is left working for a broker
adapter to route. ADR-0021 decision 6 records that this is correct: the order is
live and its capital is genuinely committed, so its reservation and contribution
stay held.

What ADR-0021 also recorded, as a known boundary, is that **nothing can end such
an order**. `apply_execution_report` bridges a venue *fill* into the pipeline. A
venue rejection, cancellation or expiry has no route home at all: the public
surface of `ExecutionPipeline` is seven methods, none of which terminates an
order. Measured, six externally routed events leave six open orders holding six
reservations, six contributions and 3,000 of committed notional, with no way to
retire any of it.

Once ADR-0023 makes a working order durable, that gap becomes load-bearing: a
restored session whose venue reports its orders dead has no way to say so.

**A second defect surfaced while designing this.** `_apply_reports` never writes
to `ExecutionState`. On the simulated path the report is recorded because
`ExecutionEngine.simulate` runs first; on the broker path it is not. Measured, a
simulated run of three fills leaves `execution.reports = 3`, while a venue fill
leaves it at `0`.

The consequence is that one `ExecutionReport`, delivered twice with the same
`execution_id` through the public `apply_execution_report`, **is applied twice**:
cash 999,600 → 999,200, position 4 → 8, `filled_quantity` 4 → 8, and two pipeline
fills for one venue execution. A full fill happens to be caught by the OMS state
machine raising `InvalidTransitionError`; a partial fill is not caught at all.

`alphalab.broker.reconciliation` was built for exactly this. It classifies a
repeated `execution_id` as `DUPLICATE` and makes it a deterministic no-op,
calling redelivery after a reconnect "the normal behaviour of a network". It
operates on `BrokerState`, and the pipeline never consults it.

---

# Decision drivers

- **D1.** "Restoration must not duplicate execution" is unachievable while the
  live path duplicates execution without restoring at all.
- **D2.** The venue is the authority for venue state. `reconcile`'s own contract
  is that it "does not resolve differences, it states them".
- **D3.** Do not persist a belief AlphaLab cannot verify. A stored belief about
  a venue is stale the instant the process stops.
- **D4.** Do not create a second authority for a derivable fact.
- **D5.** No transport in v2.9. Introducing broker infrastructure to close a
  state-modelling gap would make the durable-state design depend on external
  infrastructure that has not been architecturally specified.

---

# Decision

## 1. Persist what AlphaLab did. Do not persist what AlphaLab believes about a venue.

| State | Disposition | Why |
| --- | --- | --- |
| OMS working order and status | **persist** | AlphaLab's own record of what it sent |
| Allocation reservation and contribution | **persist** | AlphaLab's own committed capital and attribution |
| Applied venue `execution_id`s | **persist**, via `ExecutionState.reports` | what AlphaLab has already acted on |
| Routing mode and pipeline configuration | **persist** | AlphaLab's own configuration |
| Broker order handle | **reconstruct** | derivable — see decision 2 |
| `ExternalOrderMap` | **reconstruct** | a cache of that derivable fact |
| `BrokerState` | **do not persist** | an unverified belief about a venue |

## 2. The broker handle is derived, so the mapping is a cache

`broker_order_id_for` returns `f"ALB-{oms_order.order_id.value}"`, derived from
the OMS order id rather than freshly minted — its own docstring explains why: "so
a retry after a lost response addresses the *same* order at the venue instead of
creating a second one. Determinism here is a safety property, not a
convenience."

Because the handle is derived, the OMS↔broker mapping is reconstructable from
OMS state alone. `ExternalOrderMap` is therefore a cache, not an authority, and
persisting it would install a second source of truth for a fact the OMS order
already determines.

## 3. `BrokerState` is reconstructed through reconciliation, never restored

A persisted `BrokerState` would be an unvalidated second authority about the
venue, restored into a process that cannot check it. `reconcile` exists to
rebuild that belief from `remote_orders`, `remote_positions` and
`remote_account`, and the venue is the authority throughout.

Excluding it also keeps the v2.9 state design free of any dependency on broker
infrastructure.

## 4. Venue execution reports are recorded in `ExecutionState`

`_apply_reports` records the report, closing the asymmetry between the simulated
and broker paths. `ExecutionState.reports` is already a map keyed by execution
id, which is exactly the ledger a duplicate check reads; the broker path simply
never wrote to it.

This is also what makes the applied-execution set durable under ADR-0023,
without introducing any new state.

## 5. A repeated `execution_id` is a no-op, not an error

`apply_execution_report` returns the state unchanged, with empty fills and
trades, when the `execution_id` is already present in `ExecutionState.reports`.

**A no-op rather than an exception**, matching `reconciliation`'s own reasoning:
a duplicate "must be a no-op rather than an error because a reconnect makes it
routine". The pipeline mirrors that decision; it does **not** import the broker
package, so the dependency direction stays broker → pipeline.

## 6. A public terminal-outcome bridge ends a working order

A public method takes a working order to `CANCELLED`, `REJECTED` or `EXPIRED`
and routes through the functions that already exist — `_close_unfilled_order`
followed by `_release_if_terminal` — so the terminal transition has the same
consequence it has for every other outcome under ADR-0021: both ledgers retired,
exactly once, idempotently.

It answers "the venue says this order is dead" **without knowing how the venue
said it**. No transport, no connection, no adapter is involved.

## 7. What v2.9 deliberately does not do

- broker transport, credentials, or connection lifecycle;
- driving `reconcile` on resume;
- unifying `OMSState` and `BrokerState` as one order authority;
- any real venue integration.

The OMS/Broker authority question is real and remains a known internal item to
be closed before v3.0. It is not opened here, because closing it well requires
the venue release's context.

---

# Ownership

| Concept | Owner |
| --- | --- |
| The order AlphaLab sent | `OMSState` |
| Capital committed to it | `AllocationState` (ADR-0021) |
| Executions already applied | `ExecutionState.reports` |
| The venue handle | derived by `broker_order_id_for` |
| Venue truth | **the venue**, observed through `reconcile` |
| Belief about the venue | `BrokerState` — rebuilt, never restored |

---

# Data model changes

```text
+ ExecutionPipeline.apply_terminal_outcome(state, order, outcome, reason, timestamp)
      outcome in { CANCELLED, REJECTED, EXPIRED }

  _apply_reports              # now records the report in ExecutionState
  apply_execution_report      # now a no-op for an execution_id already applied

BrokerState                   # UNCHANGED, and not persisted
ExternalOrderMap              # UNCHANGED, and not persisted
broker_routing public surface # UNCHANGED
alphalab.broker               # UNCHANGED
```

No new state is introduced. The applied-execution ledger is `ExecutionState`,
which already exists and is already keyed by execution id.

---

# Boundary behavior

The pipeline gains no knowledge of brokers. `apply_terminal_outcome` takes an
outcome and a reason, not a venue message; `apply_execution_report` continues to
take an `ExecutionReport`, which `broker_routing` already knows how to build.
The direction of dependency is unchanged: the broker layer knows about the
pipeline, and the pipeline does not know about the broker layer.

---

# Persistence semantics

No new payload. The applied-execution set rides inside `PipelineSnapshot`'s
`execution` field (ADR-0023), because it is `ExecutionState`. `BrokerState` and
`ExternalOrderMap` appear in no snapshot.

---

# Testing invariants

1. A venue fill applied through `apply_execution_report` is recorded in
   `ExecutionState.reports`, as a simulated fill already is.
2. The same `execution_id` applied twice leaves cash, position, filled quantity
   and pipeline fill count unchanged, and produces no second fill or trade.
3. That holds for a **partial** fill, which the OMS state machine does not catch.
4. A working order taken to `CANCELLED`, `REJECTED` or `EXPIRED` reaches that
   OMS status and retires both its reservation and its contribution.
5. Termination is idempotent and does not raise on a second call.
6. A restored working order can be terminated the same way as one that never
   left the process.
7. No snapshot contains `BrokerState` or `ExternalOrderMap` data.
8. A working order that is still working keeps both ledgers — the ADR-0021
   guarantee is unchanged.

---

# Migration and compatibility

No data migration. One behavioural change on a public method:
`apply_execution_report` becomes idempotent for a repeated `execution_id` where
it previously double-applied. Any caller relying on the old behaviour was
double-counting.

---

# Explicit non-goals

- Broker transport, credentials, connection lifecycle, retries or backoff.
- Persisting `BrokerState`, `ExternalOrderMap`, or any venue-derived belief.
- Driving `reconcile` automatically on resume.
- Unifying `OMSState` and `BrokerState` as one order authority.
- Importing `alphalab.broker` from the pipeline.
- Inventing a fill, a cancellation, or a status the venue did not report.

---

# Consequences

Benefits. A redelivered venue fill can no longer double-count cash and
positions — a live-path correctness defect closed independently of any restore.
`ExecutionState` finally means what its name and its keying already claimed on
both paths. A restored working order can be ended truthfully. The authority
model is explicit: AlphaLab persists what it owns and rebuilds what it merely
believes.

Costs. The duplicate rule is implemented twice in the codebase — once in
`broker.reconciliation` over `BrokerState`, once in the pipeline over
`ExecutionState` — because the pipeline must not depend on the broker package.
That duplication is deliberate and is the strongest argument for the eventual
OMS/Broker authority unification, which remains deferred. A caller must still
decide *when* to terminate a restored order; v2.9 supplies the mechanism, not
the policy.

---

# Alternatives Considered

**Persist `BrokerState` alongside the pipeline state.** Rejected. It restores an
unverified belief about an external system as though it were fact, creates a
second authority `reconcile` exists to rebuild, and would make the durable-state
design depend on broker infrastructure — the stop condition this release was
explicitly checked against.

**Have the pipeline call `broker.reconciliation.classify_execution`.** Rejected.
It inverts the dependency direction: `alphalab.broker` deliberately depends on a
structural protocol rather than importing the OMS, and making the pipeline
import the broker package would undo that. Mirroring the decision costs one
membership check.

**Raise on a duplicate `execution_id` instead of ignoring it.** Rejected on the
repository's own reasoning: redelivery after a reconnect is routine, and an
exception at a routine event forces every caller to distinguish "this is fine"
from "this is not".

**Defer the whole external-order question to the venue release.** Rejected. The
duplicate defect exists today, on a public method, with no restore involved; and
ADR-0023's invariant that restoration must not duplicate execution cannot hold
while the live path duplicates it.

**Add a separate applied-execution ledger to `ExecutionPipelineState`.**
Rejected as redundant. `ExecutionState.reports` is already a map keyed by
execution id and is already what the simulated path writes; a parallel structure
would be a second answer to one question.

---

# Release impact

Minor-version feature and fix. One new public method, one behavioural change to
an existing public method, and one internal recording that closes a path
asymmetry. No new state, no new payload, no persisted format change, and no
broker infrastructure.
