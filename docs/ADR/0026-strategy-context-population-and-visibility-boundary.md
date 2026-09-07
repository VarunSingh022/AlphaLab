# ADR-0026: StrategyContext Population and the Visibility Boundary

## Status

**Accepted and implemented in v2.10.0.** `alphalab.runtime.context_views` ships
`PortfolioView`, `OrderView`, `OrderShare`, `RiskView`, `MarketView` and
`order_shares_by_strategy`; `ExecutionPipeline.process_market_event` assembles
them from the marked locals and overlays them onto the caller's factory result;
`StrategyEngine.process_event` builds a context only for a running strategy.
All four decision-2 fields ship, including both `SHOULD` fields, because both
turned out to be reference assembly over state the pipeline already held.

`alphalab/strategy/context.py` is unchanged — see the note under *Data model
changes*, which is the one place the implementation departed from this ADR's
original prose.

Written before the implementation for the same reason ADR-0025 was: the
decision that matters — *what a strategy is allowed to see, and who assembles
it* — is cheap to settle now and expensive to change once strategy authors
depend on it.

The companion to ADR-0025 and its opposite direction. ADR-0025 settles what a
strategy tells the runtime about itself; this settles what the runtime tells a
strategy about the world. Together they close the pair ADR-0023 deferred.

Depends on **ADR-0015** for who owns attribution (decision 2 is constrained by
it more than by anything else here), on **ADR-0021** for the lifetime of the
contribution ledger, on **ADR-0009** for `ExecutionPipeline` being the spine
that may compose subsystems, and on **ADR-0025** for the state a strategy keeps
when the context cannot tell it something.

---

# Context

`StrategyContext` exists. It has existed since v0.10.0, in
`alphalab/strategy/context.py`, unchanged by a single commit since the day it
was written — five weeks and roughly 140 commits before `ExecutionPipeline`
arrived. It is a frozen, slotted dataclass with nine fields, and
`docs/architecture/strategy/STRATEGY_CONTEXT.md` specifies all nine in detail,
including their exclusions, their look-ahead bounds and their construction cost.

Eight of those nine fields are typed by a supporting `Protocol` — `config` is
bare `Any` — and **six of the eight declare no methods at all**. Only
`ClockProtocol.now` and `ScopedLoggerProtocol.info`/`error` have any surface.

Every one of the thirteen construction sites in the repository — one in
production (`alphalab/reinforcement_learning/environment.py:125`), two
benchmarks, two examples and eight test modules — passes `object()` or `None`
for `portfolio`, `market`, `risk_view`, `orders`, `history` and `universe`.
`tests/integration/harness.py:92` is representative:

```text
def context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=object(), market=object(), clock=_Clock(), logger=_Logger(),
        risk_view=object(), config={"strategy_id": strategy_id},
        orders=object(), history=object(), universe=object(),
    )
```

The pipeline documents the gap against itself. From
`execution_pipeline.py:455`: the strategy "does *not* see the marked portfolio:
its context comes from the caller's `context_factory`, which this pipeline does
not populate."

So a strategy at v2.10.0 observes exactly two things: the event handed to its
hook, and whatever it closed over in its own `__init__`. It cannot see its
position, its cash, its own orders, or the price it is about to be marked at.

## What the pipeline already computes, and throws away

`ExecutionPipeline.process_market_event` marks and resyncs immediately before
dispatching, and holds both results in locals it does not pass on:

```text
execution_pipeline.py:468   market_prices = _market_prices_with_event(...)
                    :469    portfolio     = PortfolioEngine.update_market_prices(...)
                    :472    risk          = _sync_risk_from_portfolio(state.risk, portfolio)
                    :474    strategy, intents = StrategyEngine.process_event(
                    :475        state.strategy, event, context_factory, event.timestamp)
```

The marked portfolio and the resynced risk state exist, by name, two lines
above the dispatch that cannot see them. Nothing needs to be computed for this
ADR; something needs to be *handed over*.

## The one thing that is genuinely hard

"A strategy's own working orders" cannot be answered by the OMS.

`OrderBook` keeps `_by_strategy: PersistentMap[str, PersistentSet[OrderId]]`
and exposes `orders_for_strategy`, which looks like the answer and is not.
ADR-0015 decision 4 settled that an order emitted by `AllocationEngine` declares
**no** owning strategy, because netting may fold several strategies into one
order and a single-valued index cannot answer for it. Measured on this tree, a
nine-record run of one strategy:

```text
orders in book             : 3
distinct order.strategy_id : {''}
orders_for_strategy(sid)   : 0
```

The attribution is in `AllocationState.contributions` — a
`PersistentMap[str, tuple[StrategyContribution, ...]]` keyed by order id — and
it is populated exactly while the orders are live. The same run under
`ExecutionRouting.EXTERNAL`, which leaves accepted orders working:

```text
open (working) orders      : 3
orders_for_strategy(sid)   : 0
contributions entries      : 3
  order 47579bd5… -> [('9eadebe0…', '5.000000')]
```

ADR-0021 retires a contribution when its order reaches a terminal state, so the
ledger is bounded by live orders and answers only for live orders. That is a
constraint on what this ADR can promise, and decision 2 states it rather than
working around it.

---

# Decision drivers

- **D1.** One source of truth. The context shows what the pipeline already
  holds; it never computes a second answer to a question a subsystem owns.
- **D2.** ADR-0015's attribution authority is not re-litigated. If the
  allocation ledger is where ownership lives, the context reads it there.
- **D3.** Never substitute. A field classified as populated is populated or the
  construction fails — `object()` is what the current state of affairs looks
  like, and it is what this ADR exists to end.
- **D4.** Additive for callers. Thirteen construction sites exist, one of them
  in production; none may break.
- **D5.** Visibility, never authority. Seeing risk is not affecting risk;
  seeing orders is not placing them.
- **D6.** Cost is per event, not per strategy per event.
- **D7.** Populate only what the pipeline can answer *correctly*. A field that
  needs a semantic decision stays empty and says so.

---

# Decision

## 1. The pipeline populates the context, from state it already holds

`ExecutionPipeline` assembles the pipeline-owned fields of every context, from
the values it computes during the step. It does not query subsystems, does not
recompute anything, and does not cache a parallel copy of any state:

| Field | Read from |
| --- | --- |
| `portfolio` | the marked `portfolio` local at `execution_pipeline.py:469` |
| `orders` | `AllocationState.contributions` joined to `OMSState.orders` |
| `risk_view` | the resynced `risk` local at `execution_pipeline.py:472` |
| `market` | `MarketState.latest_*` and `market_prices` |

Every one of these is a reference to an already-immutable value. Nothing is
copied and no second source of truth is created: if `PortfolioState` says a
position is 15 units, the context cannot say otherwise, because it *is* that
`PortfolioState`.

## 2. What v2.10 populates, and what stays empty

| Field | v2.10 | Why |
| --- | --- | --- |
| **`portfolio`** | **MUST** | Marked positions, cash, reserved, realized P&L and the account. Already computed one statement before dispatch. The single most load-bearing field, and the one whose absence most encourages a strategy to keep its own — divergent — copy. |
| **`orders`** | **MUST** | This strategy's **live** working orders and **its own share** of each. Read through the contributions join (decision 4), not through `orders_for_strategy`, which answers `()` for every pipeline order. |
| **`risk_view`** | **SHOULD** | Buying power, current and peak NAV, daily loss, margin and exposure headroom, from the state resynced at `:472`. A reference to a frozen value, so it costs nothing beyond the assignment. Lets a strategy self-limit instead of discovering limits through rejections. Include if the seam in decision 6 lands cleanly; drop without consequence if it does not. |
| **`market`** | **SHOULD** | A read-only view over `latest_quotes`, `latest_ticks`, `latest_bars` and `market_prices` — all `PersistentMap`, all already on the state. Assembly, not machinery. |
| `clock` | caller | Unchanged. The pipeline *could* own it — it has `event.timestamp` — and probably should in v2.11, but every existing site supplies one, the RL environment supplies a meaningful one, and no defect is attributable to the current arrangement. Named here so the omission is a decision rather than an oversight. |
| `logger` | caller | Unchanged. Not pipeline state. |
| `config` | caller | Unchanged, and deliberately so — see decision 6. `StrategyState.config` is a different value and is **not** what a strategy reads; ADR-0025 decision 4 leaves it alone and so does this. |
| `history` | **DEFER** | Requires a clock-bounded accessor whose bound is enforced at construction, plus a look-ahead regression suite. `STRATEGY_CONTEXT.md` §5 is right that this is the highest-risk field in the design, and it is a release of its own. |
| `universe` | **DEFER** | Requires deciding whether membership is configuration, instrument-registry state, or a risk control. A semantic decision, not a wiring one. |

**What a strategy sees in `orders` is its share, not sole ownership.** An order
may carry several strategies' contributions. The view therefore exposes the
order together with this strategy's signed quantity in it, and never implies the
order is the strategy's alone. Pretending otherwise would contradict ADR-0015's
netting model at the one place a strategy would actually read it.

**And it is scoped to live orders.** Contributions retire at terminal state
(ADR-0021), so a strategy cannot see its own filled or cancelled history here.
That is a real limitation, it follows from a decision taken for good reasons
elsewhere, and this ADR does not widen the ledger's lifetime to work around it.

## 3. The context is built after marking, and the ordering is the guarantee

The sequence at `execution_pipeline.process_market_event` is fixed and this
decision depends on it:

```text
:468  market_prices ← this event's price folded into the known prices
:469  portfolio     ← PortfolioEngine.update_market_prices(state.portfolio, …)
:472  risk          ← _sync_risk_from_portfolio(state.risk, portfolio)
      ─────────────── context assembled here, from these locals ───────────────
:474  StrategyEngine.process_event(state.strategy, event, …)
```

**The context is assembled from the marked locals, never from `state.portfolio`
or `state.risk`.** Those are the pre-mark values, they are still in scope, and
reading them is the one mistake that would make this feature quietly wrong: the
strategy would see a book marked at the *previous* event's prices while the risk
engine evaluated its order against the current ones.

This ordering is already what the pipeline's own docstring promises — positions
are marked "*before* anything is decided on it" — and populating the context is
what finally makes that promise observable to the party it was written for.

## 4. A strategy sees its own orders and no other strategy's

The per-strategy order view is derived from `AllocationState.contributions`:

```text
for order_id, contributions in allocation.contributions.items():
    for contribution in contributions:
        index[contribution.strategy_id] += (order, contribution.quantity)
```

Built **once per event**, keyed by `strategy_id`, and each strategy's context
holds the slice for its own key. A strategy that contributed to no live order
sees an empty view — which is a fact, not a failure (decision 10).

Cross-strategy visibility is refused structurally rather than by convention:
the slice for strategy A contains no reference reachable to strategy B's orders,
so there is no accessor to misuse. This is `STRATEGY_CONTEXT.md` §4's strict
exclusion, made true.

`OrderBook.orders_for_strategy` is **not** used and must not be: it answers
`()` for every pipeline-produced order, and a context built on it would be
silently, permanently empty — the exact failure mode this ADR is meant to remove.

## 5. Read-only, with no route to authority

`StrategyContext` is `frozen=True, slots=True`, so rebinding a field already
raises `FrozenInstanceError`. That is necessary and not sufficient: a read-only
field holding a mutable `list` or `dict` is mutable by a careless strategy with
no error at all.

**Every value reachable from a context must be immutable.** The pipeline state
already satisfies this — `PersistentMap`, `PersistentSet`, `AppendOnlyLog` and
frozen dataclasses throughout — so this is a constraint on the *view* types this
ADR introduces, not a change to anything that exists. The view types expose no
setter, no `append`, no `__setitem__`, and hand out no mutable container.

**`context.orders` never becomes an order-submission API.** It is a query view
and nothing else. The only way an effect leaves a strategy is the
`Iterable[Intent]` its hook returns. A `submit` on the context would bypass
allocation and netting, bypass risk ordering, and reintroduce a mutable side
effect reachable from inside a nominally pure hook — `STRATEGY_CONTEXT.md` §3
sets this out and this decision makes it binding rather than advisory.

No concrete engine (`RiskEngine`, `OMSEngine`, `PortfolioEngine`,
`AllocationEngine`) is reachable from a context. Views expose data; engines stay
outside.

## 6. The caller's factory survives, and pipeline-owned fields win

`ContextFactory` remains `Callable[[str], StrategyContext]`. Every existing
construction site keeps working with no edit.

**The pipeline overlays its fields onto the context the caller built:**

```text
context = replace(context_factory(strategy_id), **pipeline_owned)
```

`StrategyContext` is a frozen dataclass, so `dataclasses.replace` is exactly the
right instrument, and it is verified to work on this tree's `frozen + slots`
definition.

Three consequences, all intended:

- **Pipeline-owned fields always win.** A caller that supplies a `portfolio` has
  it overwritten. That is what keeps decision 1 true: there is one source of
  truth, and a caller cannot install a second one.
- **Caller-owned fields are untouched.** `clock`, `logger` and `config` are
  passed through exactly as supplied.
- **`alphalab/reinforcement_learning/environment.py` keeps working unchanged.**
  It threads a `_PendingDecision` through `context.config` as its
  action-injection channel and its agent reads it there. `config` is caller-owned,
  so the overlay does not touch it. This is the concrete reason the factory is
  preserved rather than replaced, and any future change to `config` ownership
  must account for this consumer.

A caller driving `ExecutionPipeline` directly and supplying a factory therefore
gets a *more* populated context than before and never a less populated one.

## 7. O(1) per context, and none at all for a strategy that is not running

Two costs, and they are different:

- **Per event:** one pass to build the contributions index (decision 4), bounded
  by *live* orders because ADR-0021 retires the rest. Nothing else — portfolio,
  risk and market are already-frozen values.
- **Per context:** reference assembly only. No copying, no snapshotting, no
  cross-engine query. O(1) with respect to position count, order count and
  universe size, which is `STRATEGY_CONTEXT.md` §6.1's requirement.

**No context is built for a strategy that is not `RUNNING`.** Today
`StrategyEngine.process_event` builds one for every registered strategy at
`engine.py:33`, and `Dispatcher` checks the status afterwards at
`dispatcher.py:29`. That is free while contexts are `object()` and becomes
O(strategies × events) the moment they are not. Moving construction behind the
status check is a **prerequisite** of this ADR, not an optimization of it.

Pooling, arenas and the rest of `STRATEGY_CONTEXT.md` §6.2 are explicitly not
adopted. The requirement is O(1) assembly; anything further needs a profile
first.

## 8. The context is derived, and is never persisted

`StrategyContext` gets **no snapshot module, no schema constant, and no field on
any existing envelope.** It is a projection of pipeline state that lives for the
duration of one hook invocation.

After a restore, the context is rebuilt from the restored `ExecutionPipelineState`
by the same assembly that built it during the original run. It therefore
round-trips **for free and exactly**, with nothing to version and nothing to be
compatible with — the strongest argument for populating it from pipeline state
rather than from anywhere else.

`PIPELINE_SNAPSHOT_SCHEMA` stays at 2, and `SESSION_`, `BACKTEST_`,
`ALLOCATION_`, `OMS_`, `PORTFOLIO_` and `LIFECYCLE_SNAPSHOT_SCHEMA` do not move.
This ADR introduces no persisted state of any kind.

## 9. What this changes about strategy-owned state, without changing ADR-0025

ADR-0025 is unchanged by this decision and is not reopened by it.

What changes is how much a strategy *needs* to declare. Every fact the context
supplies is a fact the strategy no longer has to remember, and a remembered fact
is the one that desyncs: a strategy tracking its own position in a Python
attribute must keep that attribute correct across every fill, every partial,
every rejection and every restore, and nothing checks that it did. The same
number read from `context.portfolio` is correct by construction and round-trips
because the pipeline state round-trips.

So the two ADRs point the same way from opposite ends. ADR-0025 makes declared
state survive; this reduces how much state should be declared at all. The
guidance that follows — and it is guidance, not a rule this ADR can enforce —
is that `StrategyStateProtocol` is for what a strategy *derives* (a rolling
window, a fitted model, a counter of its own decisions), not for what the
runtime already knows (positions, cash, live orders, risk headroom).

## 10. A required view is constructed or the step fails; nothing is substituted

**A field this ADR classifies as populated is never `None` and never `object()`.**
That substitution is precisely the state of affairs at v2.10.0 and precisely
what this ADR exists to end; reintroducing it as a fallback would make the
feature unobservable in exactly the cases where it failed.

- **An empty view is not a failure.** A strategy with no position, or no live
  orders, gets an empty view. That is an answer.
- **A view that cannot be constructed fails the step**, raising
  `RuntimeValidationError` — the pipeline's existing construction-time error,
  the one `_require_one_account_currency` already raises — naming the field and
  the `strategy_id`. It does not fail *the strategy*: a context that cannot be
  assembled is a pipeline defect, not a strategy defect, so it must not be
  routed through `RuntimeSupervisor.fail` or set `last_error`. This mirrors
  ADR-0025 decision 12, which keeps persistence failures out of strategy status
  handling for the same reason.
- **A `DEFER` field keeps its current placeholder** and is documented as
  unpopulated. That is not a substitution: nothing claims those fields are
  populated, and a strategy reading `context.history` at v2.10 is reading
  something this ADR says is not there yet.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Assembling a context | `ExecutionPipeline`, during the step |
| Marked portfolio truth | `PortfolioState`, via the local at `:469` |
| Risk headroom | `RiskState`, via the resync at `:472` |
| Order attribution | `AllocationState.contributions` (ADR-0015 decision 4) |
| Attribution lifetime | ADR-0021 — retired at terminal state |
| Market observations | `MarketState.latest_*` and `market_prices` |
| `clock`, `logger`, `config` | the caller, through its factory |
| Immutability of every view | the view types this ADR introduces |
| Strategy-derived state | the strategy (ADR-0025) |

---

# Data model changes

```text
+ read-only view types for portfolio, orders, risk and market
+ a per-event, per-strategy order index derived from contributions
  StrategyContext                  # UNCHANGED -- nine fields, frozen, slots
  ContextFactory                   # UNCHANGED -- Callable[[str], StrategyContext]
  StrategyProtocol                 # UNCHANGED
  StrategyStateProtocol            # UNCHANGED (ADR-0025)
  StrategyState.config             # UNCHANGED
  StrategyRecord.config            # UNCHANGED (ADR-0025 decision 4)
  every snapshot schema constant   # UNCHANGED -- nothing here is persisted
  OrderBook._by_strategy           # UNCHANGED -- and deliberately unused here
  AllocationState                  # UNCHANGED -- read, never extended
```

`StrategyContext`'s field *names*, arity and **field types** do not change.
What changes is what those fields hold at runtime.

**The supporting marker protocols stay intentionally minimal, and did not gain
the views' methods.** This ADR's first draft said they would; the implementation
deliberately did not do it, and this records why rather than leaving the
omission to be rediscovered.

Giving `PortfolioSnapshotProtocol`, `OrderFacadeProtocol`, `RiskViewProtocol`
and `MarketViewProtocol` real signatures means naming `Position`, `Order`,
`MarginStatus`, `ExposureStatus`, `RiskLimits`, `Quote`, `Tick` and `Bar` in
`alphalab/strategy/context.py`. `alphalab.strategy` imports `alphalab.common`
and itself, and nothing else — it is a strict leaf, and typed protocol members
would make it depend on `portfolio`, `oms`, `risk` and `market` at once. That is
a dependency change no decision here authorises, and it is a larger commitment
than the typing convenience it buys.

So the concrete, domain-facing read-only views live **outside** the strategy
package, in `alphalab.runtime.context_views`, where those domain types are
already in scope. A strategy author who wants full static typing annotates
against the view classes directly; the runtime behaviour, the public API and
every guarantee in decisions 1-10 are identical either way.

This is an implementation choice that preserves the leaf invariant, not an
accidental omission. Revisiting it means deciding whether `alphalab.strategy`
may depend on four domain packages — a question for its own decision, not a
detail of this one.

---

# Boundary behavior

Fold the event's price into the known prices → mark the portfolio → resync risk
→ build the contributions index once → for each strategy **that is `RUNNING`**,
call the caller's factory, overlay the pipeline-owned fields, dispatch → refuse
the step if a populated field cannot be constructed, naming the field and the
strategy.

---

# Persistence semantics

None. Nothing here is captured, no constant moves, and no envelope gains a
field. A context is rebuilt from restored state and is therefore equal, by
construction, to the one the uninterrupted run held at the same boundary.

---

# Testing invariants

1. **Marked, not stale.** A strategy's hook observes the position and cash that
   result from *this* event's mark — asserted against the state after
   `update_market_prices`, and asserted to differ from the state before it, so
   the test fails if the context is ever built from `state.portfolio`.
2. **Risk is the resynced risk**, if `risk_view` ships: the buying power a
   strategy sees equals the value risk evaluates its order against on the same
   event.
3. **Own orders only.** Two strategies run concurrently with live working
   orders; each sees exactly its own, and each sees its own *share* of an order
   both contributed to.
4. **The netted case is right.** An order carrying two contributions appears in
   both strategies' views with different quantities, and neither view claims
   sole ownership.
5. **Not through the OMS index.** A test pins that the view is non-empty for a
   pipeline-produced order while `OrderBook.orders_for_strategy` is empty for
   the same order — the regression that would otherwise reappear silently.
6. **Immutability by attempt, not by inspection.** Rebinding a context field
   raises; mutating any mapping, sequence or view reachable from it raises. Not
   `isinstance` checks — actual mutation attempts.
7. **No submission path.** No attribute reachable from a context reaches an
   engine or submits an order; asserted structurally over the view types.
8. **Restored-context equality.** A context built from a restored state equals
   one built from the uninterrupted control at the same boundary — the claim in
   decision 8, tested rather than asserted.
9. **Caller factory compatibility.** Every existing construction site keeps
   working; `clock`, `logger` and `config` arrive exactly as supplied; a
   caller-supplied `portfolio` is overwritten by the pipeline's.
10. **The RL consumer still works.** `alphalab.reinforcement_learning` runs
    unchanged and its `_PendingDecision` still reaches its agent through
    `context.config`.
11. **No context for a non-running strategy.** A registered strategy in every
    non-`RUNNING` status has no context built for it — asserted by a factory
    that records its calls.
12. **O(1) construction.** Context assembly cost is flat against position count
    and live-order count; measured the way ADR-0025's capture cost was.
13. **A populated field is never a placeholder.** No context reaching a hook
    carries `None` or a bare `object()` in a field decision 2 marks MUST.
14. **Deferred fields are honestly empty**, and a test names that expectation
    rather than leaving it implicit.

---

# Migration and compatibility

Additive. No caller changes, no signature changes, no schema movement. A
strategy that ignores its context is unaffected; a strategy that reads a
`DEFER` field sees what it saw before.

The one behavioural change is intended and is the point: a strategy that reads
`context.portfolio` gets the marked book instead of an `object()`.

---

# Explicit non-goals

- **Historical data access.** Deferred with its look-ahead bound (decision 2).
- **Universe.** Deferred pending a decision on where membership lives.
- **Allocation visibility.** Reservations and contributions are *post*-intent
  facts. Showing a strategy the capital its own intent will later reserve
  invites it to pre-size, duplicating the allocation engine's authority
  (ADR-0015). The contributions ledger is read here for attribution only, and
  no allocation *decision* is exposed.
- **Mutable runtime services**, in any field, permanently.
- **Reviving `on_fill`, `on_order`, `on_timer`, `on_start`, `on_stop` or
  `on_shutdown`.** `FillEvent` and `OrderEvent` are still never constructed in
  production. Delivering them means a second strategy dispatch per event, which
  changes intent ordering and every parity baseline — not in this ADR, and not
  made easier or harder by it.
- **Runtime unification.** `SessionState` and `BacktestState` are untouched.
- **`config` semantics**, in either sense: `StrategyContext.config` stays
  caller-supplied, and `StrategyRecord.config` stays exactly as ADR-0025
  decision 4 left it.
- **Reopening ADR-0025.** Decision 9 is about how much state a strategy should
  choose to declare, not about the contract for declaring it.
- **Context pooling or arena allocation.**
- **Changing `ContextFactory`'s signature.**

---

# Consequences

Benefits. A strategy can finally see the book it is trading, at the moment it
is trading it. The single most common reason to keep a private copy of position
and cash goes away, and with it the most likely source of a strategy that
desyncs across a restore. The design document written at v0.10.0 stops
describing something that does not exist. And because the context is derived
from pipeline state, it round-trips with zero persistence work.

Costs. The pipeline gains an assembly step it did not have, and one per-event
pass over the live contribution ledger. `StrategyContext`'s empty marker
protocols gain real methods, which is the first time strategy authors can
depend on their shape — so the API surface this ADR opens is one AlphaLab will
have to keep. `orders` is limited to live orders and cannot answer for filled
ones, which will be asked for. And two of the nine fields remain empty at
v2.10, so "what does a context give me" has a version-dependent answer until
they land.

---

# Alternatives Considered

**Populate from `OrderBook.orders_for_strategy`.** The obvious route, and it is
wrong: measured on this tree it answers `()` for every pipeline-produced order,
because ADR-0015 decision 4 deliberately leaves netted orders unattributed in a
single-valued index. A context built on it would be permanently, silently empty
— and would have passed a naive test written with a hand-constructed order.

**Give `Order` an owning strategy so the index works.** Rejected: it reverses
ADR-0015 decision 4 for the convenience of one consumer, and it cannot represent
a netted order honestly. The contribution ledger exists precisely because one
order can belong to several strategies.

**Add a strategy → orders index to `AllocationState`.** Faster than the
per-event pass. Rejected: it adds persisted state, which moves
`ALLOCATION_SNAPSHOT_SCHEMA` and puts a derived index into a durable envelope —
paying a permanent schema cost for a per-event convenience. The ledger is
already bounded by live orders, so the pass is bounded too.

**Replace `ContextFactory` with pipeline-only construction.** Simpler, one
assembly path, no merge rule. Rejected: it breaks all thirteen construction
sites including `alphalab.reinforcement_learning`, whose entire action-injection
mechanism is a caller-supplied `config`. The overlay costs one `replace` call
and keeps every caller working.

**Let a caller-supplied value win over the pipeline's.** Rejected outright under
D1: it creates exactly the second source of truth this ADR is written to
prevent, and it would let a test pass with a fabricated portfolio.

**Populate all nine fields now.** Satisfies the design document in one release.
Rejected: `history` needs a clock-bounded accessor and a look-ahead regression
suite, and `universe` needs a semantic decision about membership. Four fields
populated well beats nine populated badly, and the four chosen are the ones the
pipeline can answer correctly today.

**Substitute an empty view when one cannot be built.** Rejected under D3. A
silent placeholder is what v2.10.0 already does, and it is why this gap survived
three releases without anyone noticing.

---

# Release impact

Minor-version feature, and unusually contained for one: additive to callers,
zero persisted state, zero schema movement, and no change to
`ExecutionPipelineState`, `StrategyProtocol`, `StrategyStateProtocol` or
`ContextFactory`. The surface it opens — the view types — is new public API and
is the part that will be hard to change later.
