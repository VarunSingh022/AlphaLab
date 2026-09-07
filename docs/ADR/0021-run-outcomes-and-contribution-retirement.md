# ADR-0021: Run Outcomes and Contribution Retirement

## Status

Accepted (v2.8.0). Written to disk in v2.9.0.

This decision was taken and implemented during v2.8 and was not recorded at the
time. This file records it as it was accepted and as v2.8.0 shipped it. Its
substance is not reopened here.

Completes ADR-0015, which introduced the contribution ledger and stated its
lifetime as running "from the moment allocation emits a request until that
request's order reaches a terminal state". That sentence assumed every request
becomes an order. Two paths do not, and two terminal transitions were never
routed through the retirement point. ADR-0024 adds the one remaining terminal
path this decision deliberately leaves open.

---

# Context

`AllocationEngine.allocate` opens **two** ledger entries per emitted request:

- a **reservation**, recording the capital held against it;
- a **contribution** entry, recording which strategies asked for it.

They have the same lifetime and must be retired together. Only one of them was.

`_release_if_terminal` retires both, but it returns early unless the order is in
the OMS book. A request dropped for want of a price, and a request risk refuses,
never reach the OMS at all — so for those two paths the reservation was freed and
the contribution entry was immortal. Measured at v2.7.0, forty events on each
path:

```text
unpriced        reservations 0    contributions 40
risk-rejected   reservations 0    contributions 40
```

Growth with no bound and no reader.

Separately, an order could reach a terminal state **by a route that produced no
execution report**. `_apply_reports` iterates reports and calls
`_release_if_terminal` per report, so a `NO_FILL`, `REJECTED` or `EXPIRED`
execution — which produces no report at all — never reached it. A partially
filled order was cancelled by `_withdraw_partial_remainder`, which freed the
reservation and nothing else. Measured on v2.7.0, twenty events on each of those
four paths left twenty entries apiece.

The common shape: "terminal" had more than one meaning, and only some of them
had a consequence.

---

# Decision drivers

- **D1.** A ledger with no bound and no reader is a leak, whatever it holds.
- **D2.** Attribution must survive until it is read. `_apply_report_to_portfolio`
  reads contributions at fill time to build the analytics trade record, so
  retirement must not run before that read.
- **D3.** Retirement must be idempotent, because a lifecycle can reach its end
  by more than one route and safety must not depend on which one ran.
- **D4.** A working order genuinely holds capital. Freeing a reservation because
  an order is inconvenient to track would misreport committed capital.
- **D5.** State the invariant in terms of the thing that actually has the
  lifetime.

---

# Decision

## 1. The invariant is stated over the request, not over its order

**A contribution exists exactly as long as its request does.**

ADR-0015 stated the lifetime in terms of the request's *order*, which silently
assumed every request becomes one. A request whose life ends before the OMS
still finishes, and the ledger entry it opened must end with it.

## 2. A request that never becomes an order ends at `_retire_dropped_request`

Two paths end before the OMS, and both now retire **both** ledgers:

- **unpriced** — no market price was observed for the request's asset, so it is
  dropped deterministically before reaching the OMS rather than submitted as an
  order the execution leg cannot price;
- **risk-refused** — risk declined the request, so it will never reach the OMS
  and never execute.

## 3. Every terminal order transition routes through `_release_if_terminal`

"Terminal" now has one meaning and one consequence. All of these reach it:

| Outcome | Route |
| --- | --- |
| `FULL_FILL` | `_apply_reports`, per report |
| `PARTIAL_FILL` then withdrawal | `_withdraw_partial_remainder` |
| `NO_FILL` | `_close_unfilled_order` → cancel |
| `REJECTED` | `_close_unfilled_order` → reject |
| `EXPIRED` | `_close_unfilled_order` → expire |

A non-trading outcome produces no report, so the order is closed out of the OMS
into the lifecycle state that describes what happened — cancelled, rejected or
expired — rather than left open forever awaiting a fill that will not come. The
pipeline mints a fresh order per market event and never re-works an existing
one, which is what makes withdrawal correct rather than premature.

## 4. Retirement is idempotent at every point

`_release_reservation` checks membership before releasing;
`retire_contributions` returns the state unchanged for an absent key. Calling
either more than once in an order's life is therefore safe and retires exactly
once. This is what allows one function to serve every terminal transition
without each caller having to know whether another already ran.

## 5. Attribution is read before retirement, never after

`_apply_report_to_portfolio` reads `contributions_for(order_id)` **before**
applying the fill, alongside the position's `opened_at`, because a closing fill
removes the position and the terminal transition retires the ledger — so neither
can be recovered afterwards. Retiring on the terminal transition rather than on
reservation exhaustion is deliberate: a reservation can be exhausted by a fill
while the order is still working, and a contribution describes the order, not
the capital.

## 6. An externally routed working order keeps both ledgers, and that is correct

Under `ExecutionRouting.EXTERNAL` an accepted order is left working for a broker
adapter to route. No fill is invented, the order is not closed out, and its
reservation and contribution **stay held** — because the order is still live and
that capital is still committed.

This is not a leak and must not be "fixed" by retiring on the routing decision.
A route is not an outcome.

## 7. What this decision deliberately leaves open

v2.8 provides no way to take a working externally-routed order to a terminal
state. `apply_execution_report` bridges a venue **fill** into the pipeline; a
venue rejection, cancellation or expiry has no route home, so such an order — and
both its ledgers — stay held indefinitely.

That is stated here as a known boundary of this decision, not as an oversight
inside it. **ADR-0024 supplies the missing terminal bridge.** This ADR records
v2.8 as it shipped, which did not have one.

---

# Ownership

| Concept | Owner |
| --- | --- |
| The reserved amount | `alphalab.allocation.engine.AllocationEngine` |
| The contribution index | `alphalab.allocation.state.AllocationState.contributions` |
| The *moment* a lifecycle ends | `alphalab.runtime.execution_pipeline` |
| Pre-OMS end of life | `_retire_dropped_request` |
| Terminal-order end of life | `_release_if_terminal` |
| Reading attribution | `_apply_report_to_portfolio`, before the fill |

---

# Data model changes

```text
AllocationState             # UNCHANGED shape
OrderRequest                # UNCHANGED
StrategyContribution        # UNCHANGED

+ _retire_dropped_request(state, order_id, timestamp)
  _release_if_terminal      # now retires contributions as well as the reservation
  _close_unfilled_order     # non-trading outcomes reach a terminal OMS state
  _withdraw_partial_remainder  # now routes through _release_if_terminal
```

No field is added, removed or retyped. The decision is about *when* entries are
retired, not about what they hold.

---

# Boundary behavior

A batch refused by the budget pre-check returns before any reservation or
contribution is created, so there is nothing to retire and nothing is retired.
The returned state differs from the input only by its events — history,
reservations and `notional_allocated` are untouched, so a rejected allocation
changes no prior reservation.

---

# Persistence semantics

None in v2.8. `AllocationState` is not persisted, so no payload changes and no
schema version moves. ADR-0023 gives it a snapshot, at which point these ledgers
become durable and this invariant must survive a round trip.

---

# Testing invariants

1. An unpriced request retires its contributions.
2. A risk-rejected request retires its contributions.
3. Neither ledger grows with the event count, on either pre-OMS path.
4. A filled order still retires its contributions where it always did.
5. Attribution still reads the contributions before they are retired.
6. The reservation ledger is unaffected by the change.
7. Each non-trading outcome — `NO_FILL`, `REJECTED`, `EXPIRED` — retires its
   contributions and still releases its reservation.
8. A withdrawn partial fill retires its contributions and still attributes the
   part that traded.
9. Retirement happens once and never raises on a second pass.
10. No fill or order is created or destroyed by the change.

Suites: `tests/regression/test_contributions_are_retired.py`,
`tests/regression/test_terminal_order_contributions.py`.

---

# Migration and compatibility

No data migration and no persisted format change. No public signature changes.
The behavioural change is that two ledgers now empty where one used to; every
figure a run reports — cash, positions, realized and unrealized P&L, the equity
curve, analytics — is unchanged.

---

# Explicit non-goals

- Retiring a reservation or contribution for an order that is still working.
- A terminal bridge for externally routed orders (ADR-0024).
- Persisting `AllocationState` (ADR-0023).
- Changing how attribution is computed or what a contribution records.
- Changing the budget pre-check or making `CapitalBudget` consumable.

---

# Consequences

Benefits. "Terminal" has one meaning and one consequence. Two unbounded ledger
leaks are closed, and the invariant is stated over the thing that actually has
the lifetime, so a future path that ends a request before the OMS is covered by
the rule rather than by an enumeration. Idempotence makes it safe to retire at
every point a lifecycle can end.

Costs. The correctness of the whole scheme rests on retirement being idempotent
and on attribution being read before it, which is a real ordering constraint
carried in two functions rather than enforced by a type. The externally routed
path remains without a terminal route until ADR-0024.

---

# Alternatives Considered

**Retire contributions when the reservation is exhausted.** Rejected. A
reservation can be exhausted by a fill while the order is still working, so the
contribution would disappear while the order it describes is still live — and it
would inherit the reservation's own defect rather than avoiding it.

**Retire at the point the request is emitted, since the contributions also
travel on the `OrderRequest`.** Rejected. The index exists because `history` is
an append-only log that cannot be looked up by order id without a linear scan,
and post-trade attribution reads it at fill time.

**Enumerate the terminal outcomes at each call site.** Rejected. That is the
shape the defect had: several places each deciding what "terminal" means, with
two of them omitting the ledger. One predicate, asked at every transition, is
what makes the invariant hold without a gap.

**Retire on the routing decision for external orders.** Rejected. A route is not
an outcome; the capital is genuinely committed while the order works, and
freeing it would misreport outstanding commitment — the exact defect ADR-0015
decision 1 exists to prevent.

---

# Release impact

Minor-version fix. No persisted format change, no schema movement, no public API
change. Closes two unbounded ledger leaks and unifies the meaning of a terminal
transition, while explicitly leaving the externally routed terminal path to
ADR-0024.
