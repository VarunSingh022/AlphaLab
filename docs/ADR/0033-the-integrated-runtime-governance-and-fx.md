# ADR-0033: The Integrated Runtime, Governance, and FX

## Status

**Accepted and implemented in v2.16.0.** Three capabilities that each close a
*join* rather than fill a hole. Every one of them landed on an authority that
already existed, and the property that makes them additions rather than a
rewrite is stated here and pinned by
`tests/integration/test_v216_capabilities.py::test_no_capability_moved_another_ones_boundary`:

* the live driver added **no field** to `RunState` and moved **no run schema**;
* FX added **no field** to run configuration and moved **no pipeline schema**;
* governance moved **exactly one** schema, and only its own.

Two of the three were designed years before they were built. ADR-0030's Tier-3
table listed `LiveDriver` as "(later)" and its consequences section recorded "no
live driver exists". ADR-0018 was written in v2.7, marked *Proposed — deferred*,
and recorded its seam "so that the next release starts from a decision rather
than from a rediscovery". This ADR implements those decisions; where it departs
from them, it says so and why.

Depends on **ADR-0030** for the run runtime and the driver definition,
**ADR-0012** and **ADR-0031** for the broker boundary and the venue transport,
**ADR-0018** for the governance seam, **ADR-0019**, **ADR-0020** and
**ADR-0028** for the currency rules, and **ADR-0032** for the classifications
this release is not permitted to reopen.

---

# Context

v2.15 closed five capability gaps and each landed on a boundary that already
existed. What remained were three places where two complete things did not
*meet*:

| Both halves existed | And nothing joined them |
| --- | --- |
| `route_order` / `apply_broker_execution`, a real venue transport, and `RunEngine` | the loop: `session.py` said "a live session driven by this module still produces working orders and stops" |
| `alphalab.lifecycle` recording what should run, and the execution path running things | `lifecycle/__init__.py` said "the two are joined by the caller" |
| `alphalab.enterprise`'s complete RBAC, and the lifecycle's append-only records | `git grep "from alphalab.enterprise"` outside that package returned **zero hits** |
| `CashLedger` keyed by currency, `Position` declaring its own | any rate at all |

---

# Decision

## 1. The live driver is a driver, and holds no state

`LiveSession` is Tier 3 on ADR-0030's definition, which is normative: "A driver
decides which record comes next and what clock reading to judge it by... It
holds no execution state and no run state." Every method is a `@staticmethod`
over an immutable value.

`LiveRunState` is an **aggregate of three existing authorities** — the
`RunState`, the `BrokerState`, the `ExternalOrderMap` — and adds only the two
facts that belong to neither: what each routing attempt decided and what each
returning fill turned out to be. It holds no cursor, no cash, no positions and
no order book of its own, pinned by
`test_the_live_state_holds_no_accounting_of_its_own`.

Broker fields were **not** added to `RunState`. ADR-0030 decision 2 fixes it at
eight fields, four of which are the continuation; moving it would move
`RUN_SNAPSHOT_SCHEMA` and reshape the envelope a backtest shares, to serve the
one driver that needs it.

## 2. A live step is settle, then advance, then route

The order is the contract. A fill the venue has already reported must reach the
portfolio *before* the strategy is dispatched, or the strategy reads a book that
does not know about a position it already holds. This is the same rule
`process_market_event` follows when it marks before it dispatches, and
`test_the_fill_is_settled_before_the_strategy_is_dispatched` pins it by
observing what the strategy actually saw.

## 3. Two ledgers answer two questions, and a `DUPLICATE` in one says nothing about the other

This is the defect the first implementation of `settle` had, and it is recorded
because it is the subtlest thing in the release.

* `BrokerState.executions` answers *has the venue-side bookkeeping recorded this
  fill?*
* `ExecutionState.reports` answers *has the **portfolio** booked it?*

Both are keyed by `execution_id`; both refuse their own repeat. A fill in one and
not the other is the **normal** case, not an edge one:
`RestVenueBroker.poll_executions` applies every fill it fetches to the broker
state before returning it, so by the time a caller hands it to `settle` the
venue-side ledger holds it and the portfolio has never seen it.

Gating the portfolio on the broker layer's classification therefore dropped
*every* live fill, silently. The rule now: **a break stops a fill; a `DUPLICATE`
does not.** `SettledExecution.booked` (did the portfolio move) is a different
property from `decision.applied` (did the venue-side ledger move), and both are
kept because both are asked.

## 4. The venue binding is durable, in its own envelope

`BrokerState` and `ExternalOrderMap` had no snapshot at all through v2.15. The
mapping is the single answer to "has this order already been sent?", and
`route_order` refuses a `DUPLICATE_SUBMISSION` on its evidence alone — so the
duplicate-submission gate was worth exactly as much as the mapping's durability,
which across a process boundary was zero.

`BROKER_SNAPSHOT_SCHEMA` and `LIVE_SNAPSHOT_SCHEMA` are **two nested constants,
not one merged envelope**, so a backtest payload written by this build is
identical to one written before it. `test_losing_the_mapping_is_what_duplicates_an_order`
is the counter-example that keeps the guarantee from being a coincidence.

A restore does not reconnect and does not reconcile. The restored `BrokerState`
is *what AlphaLab believed*; what the venue holds is a question only
`reconcile` against a fresh fetch answers.

## 5. The lifecycle join is a query with a refusal, not a second runtime

`run_plan` resolves a deployment into what should run; `authorize_run` refuses a
run that would serve anything else. Neither starts a process, constructs a
strategy or builds a `RunConfig`, and
`test_the_join_builds_no_state_and_starts_nothing` asserts the module names no
runtime type at all.

It does not construct the strategy, deliberately: the lifecycle records a
`StrategyDefinition` — author metadata and parameter bounds — and not code.
Mapping that to a class is the caller's knowledge, and a registry of strategy
classes here would be a plugin system this package has no business owning.

The authorization is a **function, not a field**, for the reason in decision 1:
a deployment reference on `RunState` would move a schema every driver shares.

## 6. Governance is ADR-0018's option (b), and it is required

ADR-0018 considered three ways to reach an `EnterpriseState`:

* *(a) a field on `LifecycleState`* — rejected: enterprise data inside the
  lifecycle snapshot, coupling two deliberately separate packages;
* *(c) leave the check to callers* — rejected as a permanent answer, in its own
  words: "a gate anyone can bypass by calling the function directly is not a
  governance control";
* *(b) an explicit parameter* — taken.

`Governance` is the **second positional parameter** of every entry point that
changes what is live or archived, and it has **no default**. An optional
`governance=None` that skipped the check would be option (c) wearing option
(b)'s clothes, and `test_the_dependency_is_visible_in_the_signature` refuses it.

This is a **breaking change across 70 call sites**. It was taken deliberately:
that is what a governance capability costs.

## 7. One schema bump, batched, refusing version 1

`LIFECYCLE_SNAPSHOT_SCHEMA` moves 1 → 2, carrying
`StrategyPromotionRecord.actor_id`, `DeploymentRecord.actor_id` and the
`approvals` log — exactly the batching ADR-0018 planned.

A version 1 payload is **refused, not read**. ADR-0018 considered decoding the
new fields as optional and leaving the schema at 1, and rejected it: that gives
one version two payload shapes, which is the silent misread `schema_version`
exists to prevent.

`DEFAULT_SCHEMA_VERSION` stayed at 1, which is the v2.8 de-alias finally paying
for itself. Six schema-guard tests across the suite listed the lifecycle
constant among the ones *their* release did not move; the constant was **dropped
from those lists** rather than edited to 2, because pinning another release's
constant makes every future bump edit unrelated files.

## 8. An act no principal requested records no actor

An incumbent archived because a replacement displaced it was not archived *by*
anyone. Attributing it to the deployer would say someone archived that version,
when what happened is that the ledger did. `actor_id=""` is the honest answer,
and it is why the field is a reference with an empty value rather than a
required one. `GovernedAct.attributed` tells the two apart.

`deployed_by` is untouched and is still the *model note's* own field. ADR-0018:
a note on the model version is not the deployment record. Both now exist and
mean different things.

## 9. A rollback needs no approval, and that is a decision

An approval gate exists to stop something reaching an environment that was not
reviewed. A rollback returns an environment to a version that already ran there
and therefore already passed whatever gate was in force. Requiring a fresh
approval would mean a firm whose approver is unreachable cannot take a bad
release down — a governance control that makes an incident worse is not one.
`lifecycle.rollback` is still required, so it remains an authorized act by a
named principal.

## 10. AlphaLab does not decide which environments are gated

`Governance.approval_required_in` is stated per call and defaults to empty. A
firm that gates production and not paper is as legitimate as one that gates
both, and a default either way would be an invented policy presented as an
architectural one — the same reasoning ADR-0020 used to refuse a configured FX
rate.

## 11. An FX rate is supplied, with provenance, or it is refused

ADR-0020's rejected alternative is the sharpest constraint in the release and it
survives intact:

> **Introduce a rate source with a fixed or configurable rate.** Rejected. A
> configured rate is an invented one, and a figure derived from it is exactly as
> wrong as the figure being removed, with the added cost of looking
> authoritative.

So `FxRate` requires a `source` and an `as_of`, refuses a non-positive rate,
refuses a currency against itself, and `FxRates.of` refuses two quotes for one
pair rather than picking between them. There is no default, no fallback of
`1.0`, and no rate AlphaLab computes for itself.

**No triangulation and no implicit inversion**, keeping ADR-0020's non-goals for
reasons that survive having a rate source: EUR/USD at 1.10 does not make USD/EUR
`1/1.10` — that is the mid-market identity and a real quote has two sides.
`with_inverses()` will mint them, because making every caller type both
directions invites a mistyped one, but it is a deliberate act and each derived
rate carries `derived=True`.

**A stale rate is refused rather than used.** `max_age_seconds` defaults to
`None` for the same reason as decision 10.

## 12. The rule did not fork, and the fast path is untouched

`assert_single_currency_book` is still the one implementation (ADR-0028 decision
6). What changed is that a currency it can *convert* stopped being a currency it
must refuse; a pair it was given no rate for still is, and the message now names
which pair rather than the whole book.

A **homogeneous** book takes the same code it always did, calling the unguarded
`long_value` / `short_value` after the assertion has proven the book
homogeneous. The converting path exists only for a book that is actually mixed,
so the +1.78% ADR-0028 decision 7 measured for guarding the components is paid
by nobody. `test_the_components_gained_no_guard` pins their signatures.

A converted valuation records every conversion it performed. ADR-0020 removed a
number in no currency; a number in a currency the book is not wholly in, with no
statement of how it got there, would be the same defect wearing a rate. This is
**not** the per-currency mapping ADR-0020 rejected: no existing field changed
shape.

## 13. FX does not move the settlement boundary, and the reason is now sharper

ADR-0028 decision 2 refused a permissive settlement mode because it could only
mean booking a mixed book — "the portfolio snapshot at the end of the **same
event** raises" — or converting, and "*Convert.* That is FX, and it is
deferred."

FX arriving removes the *first* objection and not the second. A pipeline that
**traded** two currencies would still be wrong, for a reason ADR-0028 did not
have to state because the boundary made it unreachable: `PortfolioState.realized_pnl`
and `commission_paid` are single cumulative scalars that name no currency, and a
run trading in two would sum them across both. Allocation sizes against a
capital budget in one currency and risk limits are stated in one.

So v2.16 closes the **valuation** gap, which is the one the documentation
actually claimed — README and ROADMAP both said "valuing *across* currencies
needs an FX rate source that does not exist here" — and names the four blockers
that stand between here and settlement-level multi-currency.

Rates are consequently **not** on `ExecutionPipelineConfig`. A quote is
time-varying data, not run configuration; fixing one for a whole run would be
wrong for a live session, and it would move `PIPELINE_SNAPSHOT_SCHEMA` to serve
a book the settlement boundary prevents the pipeline from producing.

---

# Ownership

| Concept | Owner |
| --- | --- |
| The run | `runtime.run.RunEngine` / `RunState` (ADR-0030) — **unchanged** |
| The execution step | `runtime.execution_pipeline.ExecutionPipeline` — **unchanged** |
| The live loop | `runtime.live.LiveSession` — new Tier-3 driver |
| Venue-side durability | `broker.snapshot`, paired by `runtime.live_snapshot` |
| Order out / fill back | `runtime.broker_routing` — **unchanged** |
| "Has this order been sent?" | `broker.reconciliation.ExternalOrderMap` — **unchanged** |
| What should run where | `alphalab.lifecycle`'s deployment ledger — **unchanged** |
| Whether a run may serve it | `lifecycle.execution.authorize_run` |
| Who may change what is live | `lifecycle.governance.Governance` + `alphalab.enterprise` RBAC |
| What/when/who changed | the lifecycle's own append-only records; `governance_log` reads them |
| Exchange rates | `portfolio.fx.FxRates` — supplied, never derived |
| What a mixed book is | `portfolio.valuation.assert_single_currency_book` — **unchanged**, one implementation |

---

# Testing invariants

* `tests/integration/test_live_session.py` (25) — the cycle over a real socket;
  the settle-before-dispatch order; two ledgers, three tests; durability and the
  counter-example.
* `tests/integration/test_integrated_runtime.py` (7) — deployment decision to
  venue fill; a run serving the wrong version refused; a rollback changing what
  is authorized; the join names no runtime type.
* `tests/regression/test_lifecycle_governance.py` (32) — every entry point
  checks its own permission, before anything else; the actor reaches both
  records; separation of duties; the audit trail; ADR-0018's rejected
  alternatives stay rejected; round trip.
* `tests/regression/test_fx_valuation.py` (39) — provenance refusals; no
  triangulation or silent inversion; staleness; the rule did not fork; a mixed
  book values; all three helpers agree; the components gained no guard.
* `tests/integration/test_v216_capabilities.py` (3) — all three in one
  lifecycle, three refusals on their own terms, and no capability moving
  another's boundary.

---

# Explicit non-goals

* **No second run state, and no field on `RunState`.** Decisions 1 and 5.
* **No `RunDriver` protocol.** ADR-0030 considered and rejected one; a driver is
  a shape, not an interface, and `LiveSession` does not make it one.
* **No feed abstraction for fills.** `poll_executions` is deliberately not on
  `BrokerProtocol` — "how fills arrive is a venue's business" — so `advance`
  *takes* the executions that arrived.
* **No cancel-on-disconnect and no cancel-on-finalize.** Disconnecting is not
  cancelling and `finalize` is not an operator. Each has a consequence at a
  venue and belongs to a person.
* **No authentication, credentials, IAM or federation**, and no auto-emitted
  audit events from RBAC, workspace or secret operations. ADR-0018's permanent
  non-goals, unchanged.
* **No `EnterpriseState` field on `LifecycleState`.** Option (a), still rejected.
* **No FX triangulation, caching or reference-currency hierarchy.** ADR-0020's
  non-goals, kept.
* **No settlement-level multi-currency trading.** Decision 13, with its four
  blockers named.
* **No rate feed.** AlphaLab ships no FX data, exactly as it ships no
  classification data.
* **Nothing from ADR-0032's category C.** Those classifications are frozen and
  this release did not reopen them.

---

# Consequences

**What is now true.** A deployment decision, made by a named principal and
approved by a different one, authorizes a run that routes orders to a venue and
settles the fills that come back — and survives a restart without re-sending an
order the venue already holds. A book holding two currencies values as one
figure that says which rates produced it.

**Costs.** Governance is a breaking change at 70 call sites and a refused v1
lifecycle payload. The live driver adds a second durable envelope a live
operator must store alongside the run's. FX is supplied data AlphaLab does not
have and will not invent.

**What is deliberately still open**, stated so it is not discovered later:
settlement-level multi-currency trading, with the four blockers in decision 13;
a strategy-class registry for the lifecycle join; and a rate feed.

---

# Release impact

Breaking:

| Change | Who sees it |
| --- | --- |
| `governance` is the required second argument of `promote_strategy_version`, `deploy_strategy_version`, `rollback_environment`, `retire_strategy_version` | every caller of the lifecycle's governed entry points |
| `LIFECYCLE_SNAPSHOT_SCHEMA` 1 → 2; a v1 payload is refused | anyone restoring a lifecycle snapshot written before v2.16 |

Additive: `alphalab.runtime.live`, `alphalab.runtime.live_snapshot`,
`alphalab.broker.snapshot`, `alphalab.lifecycle.execution`,
`alphalab.lifecycle.governance`, `alphalab.portfolio.fx`; `rates` parameters on
`assert_single_currency`, `assert_single_currency_book`,
`PortfolioValuation.snapshot`, `PortfolioValuation.portfolio_value` and
`NAVCalculator.calculate`, each defaulting to the empty table so behaviour is
unchanged without one; `PortfolioValuationSnapshot.conversions`; `actor_id` on
two records, defaulting to `""`.

`RunEngine`, `ExecutionPipeline`, the broker boundary, the market-data boundary
and `RunStateStore` are untouched.
