# v2.16.0 Working Handoff

**Read this first after any context compaction.** It records the baseline, the
audit's full finding list with its classification, the release contract still to
be built, and the decisions that must survive. The archaeology is expensive; do
not repeat it unless the repository has materially changed.

## Where the release stands

**v2.16.0 is implementation-complete and certified. Section 18 is the
authoritative final state.**

| Phase | State |
| --- | --- |
| **Refactor audit** (sections 1-8) | **Complete and accepted.** Classifications frozen. Do **not** reopen it unless new evidence appears in code written *this* release — which happened twice, and is section 17. |
| **1. Integrated runtime** (sections 9, 12, 14) | **complete** — live driver and lifecycle join |
| **2. Approval + RBAC + audit** (sections 10, 13) | **complete** |
| **3. True FX / multi-currency** (sections 11, 15) | **complete for valuation**; settlement deliberately not taken |
| Cross-capability integration and certification | **complete** — section 18 |

### How to read this file

It was written as the work proceeded, so it carries three kinds of statement and
they are not interchangeable:

* **Checkpoints** — figures and conclusions as of a point in the work. Sections
  1 and 7 are explicitly labelled as such. They are *not* the release state and
  are kept because the audit's conclusions were drawn against them.
* **Final certified state** — section 18, and only section 18.
* **Decisions and traps** — sections 4, 5, and the "why" subsections throughout.
  These are timeless and are the expensive part to re-derive.

Category-C findings **must not** be pulled in. Category-B findings must not be
refactored casually. ADR-0016's strategy layering and the ADR-0026/0031/0032
StrategyContext decisions remain normative.

---

## 1. Baseline

**These are the figures at the v2.15 baseline and at the *audit checkpoint*, not
the release.** They are kept because the audit's conclusions were drawn against
them. **Section 18 is the final certified state**; where the two differ, section
18 is current and this table is history.

| Fact | Value |
| --- | --- |
| Branch | `main` |
| Baseline commit | `89d6498` (`feat/v2.15-production-capabilities`) |
| Tests at baseline | **3580 passed** |
| Tests at the audit checkpoint | **3662 passed, 2 skipped** *(before the three capabilities)* |
| Ruff / format / mypy at the audit checkpoint | clean; mypy strict over 992 source files |
| Benchmarks at baseline | **49 / 50** — `benchmark_workbench.py` crashed |
| Benchmarks from the audit onward | **50 / 50** |

Read `docs/work/v2.15/HANDOFF.md` for the architecture findings that predate
this release. They are all still true; nothing in v2.16 moved a boundary that
was not moved deliberately and recorded (the one deliberate move is
`LIFECYCLE_SNAPSHOT_SCHEMA`, section 13).

---

## 2. The question this release answers

> What known internal problem is **structurally required** to fix before
> AlphaLab can honestly declare v3.0.0 stable?

Every finding is **A — required before v3.0**, **B — valid current design**, or
**C — optional post-v3.0 evolution**. Nothing is "maybe". ADR-0032 is the
permanent record; this file is the working one.

---

## 3. The findings, in full

### A — required, and fixed here

| # | Finding | Evidence that made it A |
| --- | --- | --- |
| A1 | `strategy.dispatcher` routed market events by bare class name | `alphalab.live.events.TickReceived` reached `on_tick`; the strategy read `event.tick`; the `AttributeError` was converted to a **FAILED strategy**. Demonstrated, not inferred |
| A2 | Four of six `StrategyContext` protocols declared no members | `mypy --strict` rejects `context.portfolio.cash("USD")` for a downstream user; the package ships `py.typed`; 15 sites passed `object()` |
| A3 | The Workbench could not name the tab it opened | `benchmark_workbench.py` crashed on iteration 0 at every tag back to v2.14; `Tab.is_active` existed with no view exposing it |
| A4 | The delegation scanned Studio's whole event log, by class name | O(events) per call; measured quadratic |
| A5 | `studio` / `workbench` accumulated the pre-v2.1 way | measured ~3.2x per doubling; the declared 100k workload was unreachable |
| A6 | `portfolio_optimizer` returned silently wrong allocations | `optimize_maximum_sharpe(3 symbols, 2 returns, 3x3 cov)` returned a three-asset portfolio |
| A7 | `ARCHITECTURE.md`, `ROADMAP.md` and `README.md` asserted resolved gaps | market-data convergence and `broker`/`brokers` "deferred to v2.3"; four ROADMAP entries describing work v2.15 shipped; README still said "Current Release: v2.13.0" |

(Counted as six in ADR-0032 and the changelog, where A3 / A4 / A5 are one
finding — the benchmark — and the documentation truth-up is the sixth.)

### B — valid current design, each pinned by a test

`tests/regression/test_shared_names_stay_distinct.py`, one test per row.

1. Two `OrderBook`s — `oms.book` (my working orders) and `data.feed` (market
   depth). No shared operation. `alphalab.market` already says
   `OrderBookSnapshot`.
2. Two `PortfolioEngine`s — accounting and construction. No shared operation,
   no shared authority, and only the accounting one is on the execution path.
3. `optimizer` vs `portfolio_optimizer` — parameter search vs asset weights.
   Zero names in common across their public surfaces.
4. `broker` / `brokers` — converged in v2.3. `AccountSnapshot`,
   `PositionSnapshot`, `ExecutionReport`, `OrderStatus` **are** the canonical
   classes. The two `BrokerProtocol`s differ in the state they carry.
5. Two matrix inversions — `ml.linalg` pivots (design matrix), the optimizer
   does not (SPD covariance). Sharing one joins two independent packages.
6. `kernel` — zero consumers, deprecated for v3.0, and **not** a duplicate: its
   `PortfolioState` / `PositionState` *are* the canonical types.
7. `production`, `integrations` — zero consumers, deprecated for v3.0.
8. No CLI / composition root — AlphaLab is a library; the caller owns the
   wiring, which `examples/` shows thirteen times.
9. No vectorized numerical layer — zero runtime dependencies by declaration.
10. `data.feed.Bar` vs `market.bar.Bar` — wire vs canonical, already tested.
11. `lifecycle` / `experiment_tracking` import `studio` — an apparent upward
    dependency, and the single-model rule. `StrategyDefinition` is a frozen
    dataclass importing only stdlib; the alternative is a second strategy
    declaration and a translation step, which `register_strategy`'s docstring
    already refuses.

### C — optional post-v3.0. **Do not pull these in.**

1. Ten standalone packages still accumulate quadratically (`scheduler`,
   `feature_store`, `integrations`, `distributed`, `plugins`, `reporting`,
   `optimizer`, `data`, `cluster_scheduler`, `portfolio_optimizer`). Measured:
   `scheduler` grows ~3.2–3.8x per doubling from 2,500 to 20,000 timers. Off the
   execution path, all benchmarks complete, and `ARCHITECTURE.md`'s v2.1
   conversion list has always enumerated exactly what was converted — so nothing
   documented is false.
2. `ResearchPayload.parameters: dict[str, float]` on a frozen dataclass — the
   only mutable field type in the package.
3. `brokers.protocol.BrokerProtocol` shares a name with the canonical boundary.
4. `Dispatcher.dispatch_event(event: Any)` — narrowing it requires naming the
   market types, which ADR-0016 forbids inside `alphalab.strategy`; doing it
   properly means moving hook selection to `alphalab.runtime`, a canonical API
   change that needs its own decision.

---

## 4. Decisions that must survive

1. **`alphalab.strategy` still depends only on `alphalab.common`.** This was
   tested the hard way: the dispatcher fix was first written as `isinstance`
   against `alphalab.market.events`, and
   `tests/regression/test_instrument_identity_reaches_a_fill.py::test_the_strategy_package_does_not_import_instrument_identity`
   failed, because importing the submodule initialises `alphalab.market`, which
   imports `alphalab.instrument`. ADR-0016 decision 3 is normative. **Do not
   "improve" the routing to `isinstance`.** The module-plus-name match is exact
   and `test_strategy_event_routing.py` checks it against the real types through
   `alphalab.runtime`, which may import both.

2. **Payload types in `strategy.context` stay `Any`**, for the same reason.
   `Decimal` is named because it is stdlib and it is the canonical monetary
   type. This is the line `HistoryAccessorProtocol` already drew in v2.15.

3. **The four null objects are not defaulted onto `StrategyContext`.** They
   precede `clock`, `logger` and `config`, which have no null value, and
   reordering the fields would break positional construction. `history` and
   `universe` are defaulted only because they were appended at the end in v2.15.

4. **`BookUpdated` and `SnapshotCreated` reach no hook, on purpose.** Routing a
   snapshot to `on_quote` would hand existing strategies a payload with no
   `quote`. `StrategyProtocol.on_quote`'s docstring claimed L2 and was corrected
   to the truth — docstring only, no behaviour.

5. **`studio` and `workbench` were converted; the other ten were not.** The line
   is principled: a shipped benchmark that cannot execute its declared workload
   is a defect, and `benchmark_workbench.py` was one. Nothing else was.

6. **The optimizer's inversion was examined and left alone.** Elimination
   without partial pivoting is the standard backward-stable choice for SPD
   matrices. A search over 20,000 near-degenerate covariance matrices found no
   case where `ml.linalg`'s pivoting routine was materially more accurate, and
   an exactly singular matrix is already refused. The B5 test demonstrates both
   halves rather than asserting them.

---

## 5. Traps recorded so they are not re-entered

* **The `isinstance` trap.** See decision 1. It looks like the obvious fix, it
  is caught by an existing test, and the reason is an ADR three releases old.
* **Two tests quote the code they replaced.** `test_workbench_delegation.py`'s
  name-comparison guard parses the module with `ast` rather than grepping its
  source, because the module docstring quotes
  `type(e).__name__ == "BacktestCompleted"` deliberately.
* **`AppendOnlyLog.__getitem__` with a slice is O(n).** It materialises the
  prefix. The "events this call appended" read indexes directly over
  `range(len(before), len(after))` for that reason — do not simplify it to a
  slice.
* **Release hygiene has a hole, and this is the third release to hit it.** The
  v2.15 handoff found `pyproject.toml` unbumped at the v2.14 tag and no v2.14
  CHANGELOG entry. v2.16 found `README.md` still announcing "Current Release:
  v2.13.0 / 3190 tests", `ROADMAP.md` with no v2.14 or v2.15 narrative and four
  "Not yet addressed" entries describing delivered work. **The release checklist
  must touch `README.md`'s status block and `ROADMAP.md` alongside the CHANGELOG.**

---

## 6. What was added

Production:
- `alphalab/strategy/dispatcher.py` — `MARKET_EVENTS_MODULE`,
  `MARKET_EVENT_HOOKS`, `market_hook_for`.
- `alphalab/strategy/context.py` — members on four protocols; `NoPortfolio`,
  `NoMarket`, `NoRiskView`, `NoOrders`.
- `alphalab/strategy/__init__.py` — all six null objects exported.
- `alphalab/workbench/views.py` — `active_tab`.
- `alphalab/workbench/manager.py` — `_with_one_active`; the invariant.
- `alphalab/workbench/engine.py` — `_events_appended_by`; typed result lookup.
- `alphalab/workbench/state.py`, `alphalab/studio/state.py`,
  `alphalab/studio/project.py` — canonical containers.
- `alphalab/portfolio_optimizer/optimizer.py` — `_validate_covariance` and the
  two input checks.

Tests:
- `tests/regression/test_strategy_event_routing.py` (13)
- `tests/regression/test_strategy_context_contracts.py` (30, 2 skipped)
- `tests/regression/test_workbench_delegation.py` (13)
- `tests/regression/test_optimizer_inputs_are_refused.py` (14)
- `tests/regression/test_shared_names_stay_distinct.py` (14)

Docs: `docs/ADR/0032-the-refactor-audit-and-the-v3-condition.md`, plus truth-up
in `ARCHITECTURE.md`, `README.md`, `ROADMAP.md` and `CHANGELOG.md`.

---

## 7. Measurements (audit phase)

**Checkpoint measurements, taken when the audit completed.** They are the
evidence for the audit's own fixes and are not restated later; the release-level
performance evidence is in sections 16 and 18.

| Workload | Before | After |
| --- | --- | --- |
| `benchmark_workbench` (100,000 UI cycles) | crashed on iteration 0 | 3.11s, 32,132 cycles/sec |
| Workbench session scaling (2x workload) | ~3.2x | ~2.1x |
| `benchmark_strategy_studio` (10,000 backtests) | 0.756s | 0.124s |
| Benchmarks passing | 49 / 50 | 50 / 50 |

Execution-path benchmarks were unchanged **by the audit**, as they had to be —
nothing on the canonical path changed except the dispatcher's comparison, which
is one attribute read instead of one. The three capabilities that followed did
touch the canonical path (valuation gained an `rates` parameter); their
measurement is section 16, taken separately and A/B against the stashed tree.

---

## 8. The v3.0 condition

**There is no unclassified known structural defect.** Category C is not a
refactor: it is four mechanical changes that touch no boundary and can be made
at any time. "We could improve this later" appears only there.

---

# Part II — the v2.16 release contract

Three production capabilities. The archaeology for each is below; it cost real
time and should not be repeated.

## 9. Capability 1 — Integrated runtime

### What is actually missing

Not the pieces — the **loop**. Every half exists and is tested:

| Half | Owner | State |
| --- | --- | --- |
| Order out | `runtime.broker_routing.route_order` | complete, with pre-trade gates and a derived client order id |
| Fill back | `runtime.broker_routing.apply_broker_execution` | complete, through `ExecutionPipeline.apply_execution_report` |
| Venue boundary | `broker.protocol.BrokerProtocol` | complete; `RestVenueBroker` and `PaperBroker` both satisfy it |
| Reconciliation | `broker.reconciliation` | complete: duplicate, unknown-order, terminal-order, overfill |
| The run | `runtime.run.RunEngine` / `RunState` | complete (ADR-0030) |

What nothing owns: **the cycle that joins them.** `session.py` says it outright
— "a live session driven by this module still produces working orders and
stops". The caller must decide when to route, which orders are already routed,
where `BrokerState` and `ExternalOrderMap` live across steps, when to poll, and
how to apply what comes back. ADR-0030 Tier 3 names `LiveDriver` as a *later
entry in the same table*, and the consequences section says "no live driver
exists".

### The second missing piece: venue-side durability

`RunState` captures and restores (`runtime.run_snapshot`, `RUN_SNAPSHOT_SCHEMA`).
**`BrokerState` and `ExternalOrderMap` have no snapshot at all** — verified by
`grep "def capture" alphalab/broker/`, which returns nothing. A live run that
stops therefore loses which of its orders are at the venue, which is the one
fact a restart must not guess: re-routing an order the venue already holds is
the duplicate `ExternalOrderMap` exists to prevent.

### Constraints that shape the design

* **ADR-0030 decision 1 is normative**: "A driver decides which record comes
  next and what clock reading to judge it by... It holds no execution state and
  no run state." So the live driver is **stateless**, exactly as
  `TradingSession` is, and the venue state is threaded through it.
* **ADR-0030 decision 2**: `RunState` is eight fields and four are the
  continuation. Adding broker fields to it moves `RUN_SNAPSHOT_SCHEMA` and
  reshapes the envelope every driver shares. **Do not.**
* ADR-0030 non-goals include "no change to `RunStateStore`,
  `ExecutionPipelineState`". Still binding.
* `ExecutionMode.LIVE` already exists and already selects
  `ExecutionRouting.EXTERNAL`; `RunConfig.__post_init__` already forces the
  pipeline config to agree with the mode. Nothing about mode selection needs
  inventing.

### Decided shape

`LiveRunState` is an **aggregate**, not a new authority: it holds the
`RunState`, the `BrokerState` and the `ExternalOrderMap`, each still owned by
the module that owns it today. No second cursor, no second accounting, no second
order book. `LiveSession` is stateless and transforms the aggregate, the way
`TradingSession` transforms a `RunState`.

Venue-side durability gets its own envelope beside the run's, so
`RUN_SNAPSHOT_SCHEMA` does not move and a backtest payload is unaffected.

## 10. Capability 2 — Approval + RBAC + audit

**ADR-0018 already contains the complete design.** It is "Proposed — deferred
from v2.7.0" and records the intended seam precisely so that "the later release
implements a decision rather than improvising one". v2.16 is that release.
Implement what it says; do not redesign it.

* Actor is `enterprise.Principal.principal_id`, carried as a bare `str` —
  a reference, matching `run_id` / `evidence_id` / `policy_id`.
* `StrategyPromotionRecord.actor_id: str = ""` and
  `DeploymentRecord.actor_id: str = ""`. `""` means "not recorded".
* **Schema**: de-alias `LIFECYCLE_SNAPSHOT_SCHEMA` from `DEFAULT_SCHEMA_VERSION`
  to a literal, bump to `2`, **refuse version 1** — the v2.6 `PortfolioSnapshot`
  precedent. No optional decode: that gives one version two shapes, which is the
  silent misread `schema_version` exists to prevent.
* **Permission check is option (b)**: an explicit `EnterpriseState` *parameter*
  on the governance entry points. Not a field on `LifecycleState` (option (a),
  explicitly rejected), not left to callers (option (c), explicitly rejected as
  a permanent answer: "a gate anyone can bypass by calling the function directly
  is not a governance control").
* **Two logs, one authority each.** Lifecycle records stay authoritative for
  what/when/who; `enterprise.AuditEvent` stays the standalone capability its
  docstring describes.
* **Failure semantics**: `EnterprisePermissionError` propagates unchanged and
  **nothing is written before the refusal**, matching
  `deploy_strategy_version`'s existing contract.
* ADR-0018's permanent non-goals stand: no authentication, no credential or key
  handling, no IAM or federation, no auto-emitted audit events from RBAC /
  workspace / secret operations, no `EnterpriseState` field on `LifecycleState`.

`alphalab.enterprise` has `Principal`, `Session`, `AuditEvent` with `actor_id`,
and a complete RBAC implementation (`define_role`, `grant_role`,
`permissions_for`, `has_permission`, `require_permission`) — 32 tests and, as of
v2.15, still zero production consumers.

## 11. Capability 3 — True FX / multi-currency

### The rule that must not be duplicated

`portfolio.valuation.assert_single_currency_book(cash, positions, base_currency)`
is **the** rule, written over the two components so every caller can reach it.
`assert_single_currency` delegates to it; `NAVCalculator.calculate` and
`PortfolioValuation.portfolio_value` call it directly. ADR-0028 decision 6: one
implementation, one message, one place to change. **FX must go through this one
function, not around it.**

### Which helpers refuse, and why — the table is pinned

`tests/regression/test_currency_authority.py` pins ADR-0028 decision 7:

* **Refuse** (aggregate *and* name a currency): `snapshot`, `portfolio_value`,
  `NAVCalculator.calculate`, `_risk_exposure`.
* **Do not refuse** (aggregate, name no currency — components):
  `long_value`, `short_value`, `asset_values`, `ExposureEngine.*`.
* **Do not refuse** (name a currency, aggregate nothing — keyed lookups):
  `cash_value`, `CashLedger.balance`.

Guarding the components was **measured** at +1.78% end-to-end against a 0.27%
noise floor, because `snapshot` runs twice per market event. Do not add guards
there.

### The settlement boundary already names the successor

ADR-0028 decision 2 rejects a `SettlementPolicy` enum *because* no alternative
mode existed: a permissive mode could only mean booking a mixed book (which
raises one event later) or converting — "*Convert.* That is FX, and it is
deferred." A field with one legal value "implies a rate source that does not
exist". v2.16 supplies the rate source, which is what makes a second mode
legitimate rather than decorative.

### The constraint on the rate itself

ADR-0020's rejected alternative is the sharpest constraint in the whole
capability, and it must survive:

> **Introduce a rate source with a fixed or configurable rate.** Rejected. A
> configured rate is an invented one, and a figure derived from it is exactly as
> wrong as the figure being removed, with the added cost of looking
> authoritative.

So a rate is **supplied, with provenance** — source and as-of, the shape v2.15
gave `SectorClassification` — and a valuation that used rates must say which
ones. An unsupplied rate is a refusal, never a default of 1.0. ADR-0020's
D1 is the same point: "A wrong number labelled with a currency is worse than no
number."

`MixedCurrencyValuationError` stays the refusal for *no rate*. It must not come
to mean "foreign instruments are invalid" — ADR-0020 decision 4 and ADR-0019
decision 3 both state that holding foreign-currency positions is supported.

---

## 12. Capability 1 — what is built (live driver: DONE)

`alphalab/runtime/live.py` — `LiveSession`, a Tier-3 driver on ADR-0030's
definition. **Stateless**: every method is a `@staticmethod` over `LiveRunState`,
which is an *aggregate* of `RunState` + `BrokerState` + `ExternalOrderMap` and
holds no cursor, cash, positions or orders of its own. `RunState` gained no
fields and `RUN_SNAPSHOT_SCHEMA` did not move — both pinned by tests.

`advance` is `settle → RunEngine.advance → route_working_orders`, in that order,
because a fill already known must reach the portfolio before the strategy is
dispatched. Pinned by `test_the_fill_is_settled_before_the_strategy_is_dispatched`.

`alphalab/broker/snapshot.py` (`BROKER_SNAPSHOT_SCHEMA = 1`) and
`alphalab/runtime/live_snapshot.py` (`LIVE_SNAPSHOT_SCHEMA = 1`) make the venue
side durable. Two nested constants, not one merged envelope, so a backtest
payload is untouched.

### The defect the first draft had, recorded because it is subtle

**Two ledgers answer two different questions, and I conflated them.**

* `BrokerState.executions` — "has the venue-side bookkeeping recorded this fill?"
* `ExecutionState.reports` — "has the *portfolio* booked it?"

Both are keyed by `execution_id`; both refuse their own repeat. The first draft
gated the portfolio application on the broker layer's classification, so every
live fill was silently dropped — because `RestVenueBroker.poll_executions`
*applies* each fill to the broker state before returning it, making every fill
that arrives that way a `DUPLICATE` at the broker layer and entirely new to the
portfolio.

The rule now: **a break stops a fill; a `DUPLICATE` does not.** Each layer
refuses its own repeat. `SettledExecution.booked` (did the portfolio move) is a
different property from `decision.applied` (did the venue-side ledger move) and
both are kept. Pinned by three tests under "the two ledgers, which are not one
ledger".

### The second half of capability 1 — **since built; see section 14**

The paragraphs below are the plan as written before it was built, kept for the
sequencing decision, which is the part worth not re-deriving.

**The lifecycle join.** `alphalab.lifecycle`'s own docstring read: "A deployment
names what should run; running it is the execution path's job, **and the two are
joined by the caller**." Nothing checked that a run was serving the version an
environment actually had live, and there was no way to start a run *from* a
deployment.

**Sequencing decision:** this was built *after* capability 2, not before. Both
touch `alphalab/lifecycle`, capability 2 bumps `LIFECYCLE_SNAPSHOT_SCHEMA` and
adds `actor_id` to two persisted records, and doing the join first would have
meant editing the same files twice and rewriting the join's tests around a
schema that moved underneath them. The join carried the actor through for free,
which is what section 14 shipped.


---

## 13. Capability 2 — what is built (DONE)

ADR-0018's seam, implemented as recorded rather than redesigned.

`alphalab/lifecycle/governance.py` — `Governance(enterprise, actor_id,
approval_required_in)`, five permissions, `ApprovalRecord`, `approval_for`
(separation of duties), `GovernedAct` + `governance_trail`.

**Option (b), and required.** `governance` is the **second positional
parameter** of `promote_strategy_version`, `deploy_strategy_version`,
`rollback_environment`, `retire_strategy_version` and the new
`approve_deployment`. It has **no default**, pinned by
`test_the_dependency_is_visible_in_the_signature`: an optional `governance=None`
that skipped the check would be option (c) — "a gate anyone can bypass by
calling the function directly" — wearing option (b)'s clothes.

**Breaking change, 70 call sites** across 7 files updated. That is what a
governance capability costs and it was taken deliberately.

**Schema.** `LIFECYCLE_SNAPSHOT_SCHEMA` 1 → **2**, carrying
`StrategyPromotionRecord.actor_id`, `DeploymentRecord.actor_id` and the
`approvals` log. A version 1 payload is **refused**, never read: ADR-0018
rejected optional decoding at schema 1 because it gives one version two shapes.
`DEFAULT_SCHEMA_VERSION` stayed at 1 — which is the v2.8 de-alias finally paying
for itself, now asserted in `test_lifecycle_schema_is_not_an_alias.py`.

**Six schema-guard tests across the suite had to be updated**, and how matters:
each pinned "*my* release moved nothing else" and listed the lifecycle constant
among the others. The lifecycle constant was **dropped from those lists** rather
than edited to 2, because pinning another release's constant makes every future
bump edit unrelated files. Each keeps the property its own ADR promised.

### Decisions worth not re-litigating

* **`deployed_by` is untouched.** It is the *model note's* own field and is not
  the governance actor; ADR-0018 says a note on the model version is not the
  deployment record. Both now exist and mean different things.
* **An automatic move records no actor.** An incumbent archived because a
  replacement displaced it was not archived *by* anyone. `actor_id=""` is the
  honest answer and is why the field is a reference with an empty value rather
  than a required one. Pinned.
* **A rollback needs no approval.** It returns an environment to a version that
  already ran there and already passed whatever gate was in force. Requiring one
  would mean a firm whose approver is unreachable cannot take a bad release
  down. `lifecycle.rollback` is still required.
* **AlphaLab does not decide which environments are gated.**
  `approval_required_in` is stated per call. A default either way would be an
  invented policy presented as an architectural one — the same reasoning
  ADR-0020 used to refuse a configured FX rate.
* **Two logs, one authority each.** Nothing here writes into
  `enterprise.AuditEvent`, and no governance entry point returns an
  `EnterpriseState`. Pinned by signature inspection.


---

## 14. Capability 1, second half — the lifecycle join (DONE)

`alphalab/lifecycle/execution.py` — `run_plan(state, environment)` resolves a
deployment into what should run; `authorize_run(state, environment, reference,
strategy_id)` refuses a run that would serve anything else.

**A query with a refusal, not a second runtime.** It names no runtime type at
all, pinned by `test_the_join_builds_no_state_and_starts_nothing`. It
deliberately does **not** construct the strategy (a `StrategyDefinition` is
metadata, not code — a class registry here would be a plugin system this package
has no business owning) and does **not** build a `RunConfig` (an account, budget
and risk limits are operational configuration a deployment has never carried).

The authorization is a **function, not a field**: a deployment reference on
`RunState` would move `RUN_SNAPSHOT_SCHEMA`, which every driver shares.

"Nothing is live here" is a **refusal**, not `None` — every caller is about to
start a run, and a `None` a caller forgets to check starts one anyway.

## 15. Capability 3 — what is built (DONE)

`alphalab/portfolio/fx.py` — `FxRate`, `FxRates`, `FxConversion`, `NO_RATES`.

Every constraint traces to ADR-0020's rejected alternative ("a configured rate is
an invented one"):

* a rate requires a `source` and an `as_of`; a non-positive rate, a currency
  against itself, and two quotes for one pair are each refused;
* **no triangulation, no implicit inversion**. `with_inverses()` mints the
  opposite direction as a *deliberate* act and marks it `derived=True`; a real
  quote is never replaced by a derived one;
* **a stale rate is refused, not used.** `max_age_seconds` defaults to `None`
  because AlphaLab does not know a desk's tolerance — the same reasoning as
  `Governance.approval_required_in`.

`assert_single_currency_book` gained a `rates` parameter and is **still the one
implementation**. The homogeneous path in `snapshot` and `NAVCalculator.calculate`
is byte-for-byte what it was, so ADR-0028 decision 7's measured +1.78% for
guarding the component sums is paid by nobody.
`PortfolioValuationSnapshot.conversions` makes a converted figure attributable —
additive, and **not** the per-currency mapping ADR-0020 rejected.

### The scope line, and why it is where it is

v2.16 closes **valuation**, which is the gap README and ROADMAP both documented
("valuing *across* currencies needs an FX rate source that does not exist here").

It does **not** move the settlement boundary. ADR-0028 decision 2 refused a
permissive mode because a mixed book would make the next snapshot raise, or
because converting "is FX, and it is deferred". FX removes the first objection
and not the second, and the four blockers are named in ADR-0033 decision 13:

1. `PortfolioState.realized_pnl` is a single cumulative scalar naming no currency;
2. so is `commission_paid`;
3. allocation sizes against a capital budget stated in one currency;
4. risk limits are stated in one currency.

A run that *traded* two currencies would sum 1 and 2 across both. Half-building
that is exactly the "wrong number that looks right" the whole currency ADR series
exists to prevent.

**Rates are deliberately not on `ExecutionPipelineConfig`.** A quote is
time-varying data, not run configuration; fixing one for a whole run is wrong for
a live session, and it would move `PIPELINE_SNAPSHOT_SCHEMA` to serve a book the
settlement boundary prevents the pipeline from producing. Pinned by
`test_the_pipeline_config_carries_no_rate_table`.

## 16. A performance regression found and fixed during certification

The execution-pipeline benchmark read ~+5% after the FX work. Cause: `snapshot`
walked the book **twice** per call -- once inside `assert_single_currency_book`
and once in `foreign_currencies` to choose its path -- and `snapshot` runs twice
per market event.

Fixed by having the rule **return** what it found rather than having the caller
recompute it: `assert_single_currency_book` and `assert_single_currency` now
return `(foreign_positions, foreign_cash)`, and a caller that only wants the
refusal ignores the value. One walk, and the branch is free.

Measured after, A/B against the stashed pre-v2.16 tree on the same machine in the
same minute:

| | 1,000 events |
| --- | --- |
| pre-v2.16 | 0.2517 / 0.2542 / 0.2567 s |
| v2.16 | 0.2540 / 0.2565 / 0.2567 s |

+0.6% on the means, inside run-to-run noise.

**Do not compare these against readings taken at another time.** Earlier in the
work this same benchmark read ~0.2500s on both sides; the machine drifted warmer
between then and this measurement, which moved *both* arms together. That is
exactly why the comparison was taken as a paired A/B against a stashed tree in
the same minute rather than against a before-number reused from a previous
session. A reading without its paired control is not evidence here.

**Re-measured at the final acceptance review**, same method, after the two
defects in section 17 were fixed:

| | 1,000 events |
| --- | --- |
| pre-v2.16 (stashed) | 0.2517 / 0.2542 / 0.2567 s |
| v2.16 final | 0.2507 / 0.2522 / 0.2520 s |

At or slightly better than baseline. The keyed-lookup fix in section 17 removed a
linear scan from the fill path, which is part of why.

## 17. Two defects the acceptance review found, in v2.16's own new code

Recorded because both were in code written *this* release and neither was caught
by the suites that covered it. They are not reopenings of ADR-0032; they are new
evidence from the implementation, which is the one thing the freeze allows.

**1. The fill path scanned the order book.** `_oms_order_for` walked
`oms.orders.orders()` comparing stringified ids, where `OrderBook.contains` /
`.find` are keyed lookups over the `PersistentMap` v2.2 introduced for exactly
this reason. Correct but linear, on the path every venue fill takes. Fixed to
the keyed lookup; pinned by
`test_a_fill_finds_its_order_by_key_and_never_by_scanning`.

**2. FX conversions produced money without rounding like money.**
`alphalab/portfolio/money.py` is explicit: every monetary amount is an exact
multiple of the minor unit and `to_money` is the only rounding point. A rate is a
*price* -- 500.00 EUR at 1.087343 is 543.6715 USD, which is not a number of
cents -- and `convert` never quantized. So a converted `equity` came back as
`3839.74880000` while a single-currency one came back as `2100.00`: the same
field with two shapes, and sub-cent digits in a figure a human reads.

**Why the tests missed it, which is the useful part.** `Decimal` equality is
numeric, so `Decimal("3860.0000") == Decimal("3860.00")` is `True` and every
assertion passed. The property had to be written against the *exponent*, not the
value. `_is_money` in `test_fx_valuation.py` now does that.

Fixed at the single rounding point money.py mandates -- per conversion, not on
the total, which is rule 2 ("round once at entry") applied unchanged; rounding
the sum instead would be the second independent rounding that policy exists to
remove. `FxConversion.rounding` keeps what it cost, so a reconciliation can
account for it.

## 18. Final certified state

**This section is authoritative.** Every figure above it is either a v2.15
baseline or an intermediate checkpoint, and each is labelled as such where it
appears. Nothing above is restated here; where they differ, this is current.

### What is complete

| | |
| --- | --- |
| v2.16.0 implementation | **complete** |
| Refactor audit (ADR-0032) | **complete; classifications frozen** |
| 1. Integrated runtime | **complete** — live driver *and* lifecycle join |
| 2. Approval + RBAC + audit | **complete** |
| 3. True FX / multi-currency | **complete for the valuation scope**; settlement deliberately not taken (section 15) |
| `benchmark_workbench.py` | **fixed**, running its full declared 100,000-iteration workload |
| Cross-capability integration | **complete** (`tests/integration/test_v216_capabilities.py`) |

### Certified gates

| Gate | Result |
| --- | --- |
| `pytest -q` | **3767 passed, 2 skipped** (baseline 3580) |
| `mypy .` | **clean**, 1003 source files |
| `ruff check .` | **clean** |
| `ruff format --check .` | **clean**, 1068 files |
| `git diff --check` | **clean** |
| `python -m build` + `twine check` | **wheel + sdist validated at 2.16.0** |
| Examples | **13 / 13** |
| Benchmarks | **50 / 50** |
| Canonical hot path | **A/B within noise** — see section 16 for the paired readings |
| Known **required** internal refactors | **zero** |

### The v3.0 acceptance condition

ADR-0032's classification is the baseline and was **not** reopened. The three
capabilities added no unclassified structural defect: each landed on an
authority that already existed, and
`test_no_capability_moved_another_ones_boundary` pins the one property that shows
it — the live driver moved no run schema, FX moved no pipeline schema, and
governance moved exactly one, its own.

Two defects *were* found during acceptance review, both in v2.16's own new code
and neither a reopening of ADR-0032. They are section 17, and both are fixed.

### What remains open, and what kind of thing each is

**Intentional boundaries — external, not AlphaLab-internal.** Nothing to fix
here; each depends on access AlphaLab does not have.

* **No commercial venue verification.** The transport and adapter are written to
  protocol and exercised over real sockets against protocol-faithful local
  servers. No named vendor's request shapes are implemented and nothing has been
  run against a commercial venue, because this environment has no network egress
  and holds no vendor credentials.
* **No FX rate data**, and **no classification data**. AlphaLab ships the
  mechanism and refuses to invent the inputs.
* **No supervised live *process*.** Restart policy, alerting and scheduling are
  an operator's concern. `live_health` answers "should a human look at this?";
  acting on the answer is the caller's.
* **No identity provider.** ADR-0018's permanent non-goals: authentication,
  credential handling, IAM, federation.

**Category B — valid current design.** Frozen by ADR-0032 and pinned by
`tests/regression/test_shared_names_stay_distinct.py`: the two `OrderBook`s, the
two `PortfolioEngine`s, `optimizer` vs `portfolio_optimizer`, the converged
`broker` / `brokers` boundary, the two matrix inversions, the three deprecated
zero-consumer packages, the absent CLI, the absent vectorized layer,
`data.feed.Bar` vs `market.bar.Bar`, and `lifecycle` taking `studio`'s single
`StrategyDefinition`. **Do not refactor these casually.**

**Category C — optional post-v3.0 evolution. Not pulled into v2.16, and not to
be.** ADR-0032's four: quadratic accumulation in ten standalone packages
(`scheduler` measured at ~3.2–3.8x per doubling), `ResearchPayload.parameters` as
a mutable field on a frozen dataclass, the `BrokerProtocol` name shared between
`broker` and `brokers`, and narrowing `Dispatcher.dispatch_event`'s `event: Any`.

**Future evolution added by v2.16**, recorded the same way and equally optional:
settlement-level multi-currency (the four blockers in section 15), a
strategy-class registry for the lifecycle join, and an FX rate feed.

None of the above is a required refactor. "We could improve this later" appears
only in the last two groups.
