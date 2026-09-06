# ADR-0015: Allocation Authority and Attribution Truth

## Status

Proposed (v2.6).

Extends ADR-0012's `ExecutionRouting.EXTERNAL` — which made an accepted order stay
working and its reservation stay held — by making that held capital actually
count. Extends ADR-0014's explicit `schema_version` with the first real schema
evolution. Depends on ADR-0008 for the canonical `OrderRequest` this release
changes.

---

# Context

Two defects on the production execution path, both re-verified against v2.5.0
(`c7a85e8`) rather than taken from a document.

## Outstanding capital is tracked but not enforced

`AllocationEngine.allocate` computes the batch's notional and compares it to the
budget:

```python
available_cap = state.budget.available_global_capital
if total_notional > available_cap or total_notional > state.budget.maximum_exposure:
```

`state.notional_allocated` — the total of every reservation still held — is not
in that expression. The budget is therefore re-offered in full on every market
event, no matter how much capital is already committed to orders that have not
settled.

Under `SIMULATED` routing this is invisible: an order allocates, executes and
consumes its reservation inside one event, so `notional_allocated` is back to
zero before the next batch is sized. Under `EXTERNAL` routing — the routing
ADR-0012 introduced for live — the order stays working and the reservation stays
held, by design. Nothing then stops the next event allocating the whole budget
again.

Reproduced end to end, six quotes, one asset, `ExecutionRouting.EXTERNAL`:

| | |
| --- | --- |
| `budget.available_global_capital` | 1,000,000 |
| cash | 1,000,000 |
| outstanding orders | 6 |
| `notional_allocated` | **5,400,000** |
| positions held | none |
| `BudgetExceeded` events | **0** |

Risk does not catch it either, and structurally cannot: `_sync_risk_from_portfolio`
recomputes `RiskState.exposure` from `portfolio.positions` and `buying_power` from
the cash ledger on **every** event. Both are projections of *settled* state. No
fill has occurred, so cash is untouched and exposure is empty; `check_buying_power`
and `check_exposure` see a flat, fully funded account and approve each order in
turn.

### A reservation also leaks on a full fill with adverse slippage

Found while verifying the above, and a hard dependency on fixing it.
`apply_execution` consumes `min(reserved, executed_notional)`. A reservation is
`quantity * reference_price`; the executed notional is `fill_quantity * fill_price`.
When slippage moves the fill price *against* the reference — a SELL filling below
the mid — the executed notional is smaller than the reservation and a residual is
left behind. The order reaches `FILLED`, which is terminal, so
`_withdraw_partial_remainder` (which only fires on `PARTIALLY_FILLED`) never runs
and nothing ever releases it.

With `PercentageSlippage(0.01)`, thirty buy/sell round trips leave 3,000 of
phantom commitment against a 1,000,000 budget, in a run that ends holding nothing.
It accumulates monotonically and without bound. It is invisible today because
`notional_allocated` is not read by anything that decides — which is precisely the
defect above. The moment the budget guard reads it, this leak becomes a
*behavioural* bug in backtests: a long enough run refuses to allocate against
capital that is not committed to anything.

## Strategy identity is destroyed before netting

`IntentAllocator.size_intents` receives `Intent` objects carrying `strategy_id`
and returns `(instrument, quantity)` pairs. The strategy is dropped there — one
statement, before `NettingEngine` is ever reached. `allocate` then stamps every
emitted request:

```
strategy_id="ALLOC-NETTED",   # a keyword argument in the OrderRequest call
```

`"ALLOC-NETTED"` is not a strategy. It is a constant, and it is applied to *every*
request, including one produced from a single intent with no netting of any kind.
It travels unchanged into `OMSOrder.strategy_id` (and therefore into the order
book's `_by_strategy` index), into `OrderInstruction`, into
`ExecutionReport.strategy_id`, into `TradeRecord.strategy_id`, and finally into
`AttributionMetrics.pnl_by_strategy`.

Reproduced: two strategies, `MOMENTUM` intending +60 and `MEANREV` intending +40,
netting to one BUY 100 that later closes for 1,000 of realized P&L.

```
AttributionMetrics.pnl_by_strategy == {'ALLOC-NETTED': Decimal('1000.00')}
```

Every strategy-level performance number this repository can produce is a number
about a strategy that does not exist.

## Two further fabrications in the same record

`_trade_record` also hard-codes `sector_id="UNCLASSIFIED"` and
`holding_period_seconds=0.0`. Both are recorded as deferred "D3" in
`docs/ARCHITECTURE.md`. They differ in kind, and v2.6 must treat them
differently: the holding period is **derivable** from state the portfolio engine
already has, and the sector is **not knowable** at all, because no security master
exists anywhere in this repository.

---

# Decision

## 1. `CapitalBudget` stays a sizing parameter; commitment lives in state

`CapitalBudget` remains what it is — an immutable statement of how much capital a
run may deploy — and is never consumed, decremented or replaced.
`AllocationState.notional_allocated` remains the running commitment, and the
budget guard becomes:

```
state.notional_allocated + total_notional  <=  budget.available_global_capital
state.notional_allocated + total_notional  <=  budget.maximum_exposure
```

The alternative — making the budget itself shrink as orders are reserved — was
rejected on four grounds, each drawn from what the sizing models already mean:

- **It corrupts the sizing models.** `TargetWeightSizing` computes
  `available_strategy_capital * target * strength`, where `target` is a *weight*.
  Against a shrinking budget, "50%" silently becomes "50% of whatever is left",
  so the same intent means a different thing on every event.
  `EqualWeightSizing` divides capital by the asset count; against a shrinking
  budget the "equal" weights become unequal and depend on which asset was sized
  first.
- **It applies to three of five sizing models.** `FixedQuantitySizing` and
  `FixedDollarSizing` never read the budget, so a consumable budget would change
  order quantities for some sizing models and not others.
- **It is not expressible without restructuring `allocate`.** Sizing runs over the
  whole batch before netting, and reservations are created after the budget check.
  Consumption during sizing requires a sequential per-intent loop with interleaved
  reservation — and makes quantity depend on intent iteration order, which
  `tests/integration/test_backtest_replay_parity.py` relies on not happening.
- **The repository already located commitment in state.** ADR-0012: an externally
  routed order's "allocation reservation stays held — because the order is live
  and that capital is still committed." `AllocationState`'s own docstring calls
  `reservations` "the per-order ledger of capital still held". The design is
  already this one, minus the enforcement.

The separation this preserves is four-way and deliberate:

| Concept | Where it lives |
| --- | --- |
| sizing budget | `CapitalBudget` (immutable configuration) |
| outstanding capital commitment | `AllocationState.reservations` / `notional_allocated` |
| settled portfolio exposure | `PortfolioState.positions` -> `RiskState.exposure` |
| risk limits | `RiskLimits` |

A strategy may still *ask* for more than remains. It is refused, and the refusal
is a `BudgetExceeded` event. That is the honest outcome: a consumable budget would
have silently returned a smaller order with no record of why.

## 2. Allocation owns unsettled capital. Risk owns settled exposure.

One authority, and no other subsystem computes an outstanding-commitment figure.

### The reservation lifecycle has three phases, and only one of them is invalid

A reservation is created by `AllocationEngine.allocate` — **before** risk runs and
**before** anything is submitted to the OMS. That ordering is deliberate (see
decision 3), and it means a reservation legitimately exists for a request that has
no OMS order at all. Any invariant phrased as "every reservation belongs to an
open order" is therefore false in a state the architecture creates on purpose.

| Phase | OMS order | Reservation held | Verdict |
| --- | --- | --- | --- |
| **pre-OMS** — inside `_process_requests`, before submit | none | yes | **legitimate.** The request may still be released (unpriced, or risk-rejected) or submitted |
| **working** — `NEW`, `PENDING`, `ACCEPTED`, `PARTIALLY_FILLED`, `CANCEL_PENDING` | open | yes | **legitimate.** This is committed capital, and it is what ADR-0012 means by an externally routed order holding its reservation |
| **terminal** — `FILLED`, `CANCELLED`, `REJECTED`, `EXPIRED` | closed | yes | **INVALID.** This is defect A′ |

### The invariants

```
I1  (AllocationState, universal)
    notional_allocated == sum(reservations.values())

I2  (ExecutionPipelineState, universal)
    No terminal OMS order holds an allocation reservation.
      for every order in oms.orders.orders() where order.is_closed:
          str(order.order_id.value) not in allocation.reservations

I3  (ExecutionPipelineState, postcondition of a completed pipeline step)
    Every reservation belongs to an OMS order.
      for every order_id in allocation.reservations:
          order_id in oms.orders
```

**I2 is the one that catches A′, and it is universal** — it is silent about the
pre-OMS phase (no terminal order exists to name a reservation) and silent about
working orders, so it rejects nothing legitimate. Verified against every phase:
it fails on exactly the two A′ cases (a full fill below the reference price under
`SIMULATED`, and the same through `apply_execution_report` under `EXTERNAL`) and
holds on the other seven, including an `EXTERNAL` order left `ACCEPTED` while
holding its reservation.

**I3 is deliberately not universal.** It is false in the pre-OMS window, by
design — verified: immediately after `allocate` there is one reservation and zero
OMS orders. It is true of every state a public pipeline function returns, because
by the time `_process_requests` returns, each request has been either released
(unpriced, risk-rejected) or submitted. I3 is what generalises the v2.2 leak that
`tests/regression/test_risk_reservation_leak.py` guards: a request dropped from a
batch without releasing its capital.

I1 and I3 hold on v2.5.0. **I2 does not** — that is defect A′.

### A′: a terminal order releases whatever it still holds

`apply_execution` consumes `min(reserved, executed_notional)`. A reservation is
denominated at the **reference** price and consumption at the **execution** price,
so a fill below the reference leaves a residual — and the order is `FILLED`, which
`_withdraw_partial_remainder` does not cover, because it only fires on
`PARTIALLY_FILLED`.

The release therefore happens at the **terminal OMS transition**, through the
existing membership-guarded `_release_reservation`, on both routings. The guard is
what makes it idempotent against the release points that already exist: an
unguarded second release raises `UnknownReservationError`, and two existing tests
assert exactly one release event on paths that already release.

This restores I2. It does not attempt to make the two units of account agree —
re-pricing reservations on every fill is a larger change, and the asymmetry is
recorded above rather than silently fixed.

**Risk does not own it, and must not learn about it.** `RiskState.exposure` and
`RiskState.buying_power` are recomputed from the portfolio and the cash ledger on
every event by `_sync_risk_from_portfolio`; anything written into them about
working orders is erased on the next event and double-counted the moment the
order settles into a position. Making risk aware of working orders means making
risk depend on allocation, and then two subsystems answer the same question.

The choice falls to allocation on positive evidence, not by elimination:
`reservations` is already a per-order ledger; `release_reservation` already takes
no amount and raises `UnknownReservationError` rather than silently subtracting;
`tests/regression/test_risk_reservation_leak.py` already asserts exactly-once
release. Exactly-once is what makes an authority authoritative.

I1 is currently true and unasserted; v2.6 asserts it. I3 is currently true and
unasserted; v2.6 asserts it at step boundaries. I2 is currently false, and v2.6
makes it true.

## 3. An over-budget batch is refused whole, and changes nothing

The existing failure semantics are already atomic and are kept:

```
rejected allocation changes no prior reservation
```

`history`, `reservations` and `notional_allocated` are identical before and after
a refused batch; only `events` grows, by one `BudgetExceeded`. Its existing fields
are reused with honest values — `requested_notional` is the batch, and
`available_budget` becomes the *remaining* budget
(`available_global_capital - notional_allocated`) rather than the configured one.

Partial sizing and per-order rejection were both rejected. A netted order is one
order standing for many intents, so "reject only the offending orders" has no
principled ordering; either variant makes the emitted quantity depend on iteration
order; and whole-batch rejection is the behaviour
`tests/unit/allocation/test_allocation.py::test_engine_budget_breach` already
asserts.

Reservation stays where it is — created by `allocate`, before risk runs. Moving it
after risk would mean the budget guard ran against un-reserved capital and that two
orders in one batch could not see each other.

`BudgetExceededError` is exported from `alphalab.allocation` and is raised
nowhere. `allocate` returns rather than raises, and changing that is a breaking
behavioural change with no benefit. It is recorded here as dead public API and
marked for removal in v3.0, not resurrected.

## 4. Strategy contributions are carried; `"ALLOC-NETTED"` stops being reported

A netted order genuinely represents several strategies, so one `strategy_id`
cannot describe it. The information is not missing, only discarded:
`size_intents` holds `(strategy_id, instrument, quantity)` and returns two of the
three.

```python
@dataclass(frozen=True, slots=True)
class StrategyContribution:
    strategy_id: str
    quantity: Decimal  # signed, in the asset's units
```

`OrderRequest` gains `contributions: tuple[StrategyContribution, ...] = ()`,
ordered by `strategy_id` ascending so the tuple is canonical regardless of the
order intents arrived in.

### P&L splits by signed contribution over the net

```
weight_i = contribution_i.quantity / net_quantity
```

The weights sum to exactly 1, and `net_quantity` is never zero because
`allocate` already skips an asset that nets flat — a completely offset pair emits
no order, produces no fill, and has nothing to attribute.

Splitting by *absolute* contribution (`|q_i| / Σ|q_j|`) was rejected: it keeps
weights inside `[0, 1]`, but it hands a strategy that wanted to **sell** a
positive share of a **long** position's gain. Bounded and wrong is worse than
unbounded and right.

The unboundedness is real and is not an error. `MOMENTUM +60` against
`MEANREV -59` nets to 1: `MOMENTUM` carries 60 units of exposure, 59 of which are
crossed internally against `MEANREV`, and the weights `+60 / -59` say exactly
that. Those magnitudes are what internal crossing means.

Contributions are stored as **signed quantities, not precomputed weights**.
Quantities are exact inputs; weights are derived, and deriving them once at the
point of use avoids a second rounding policy. When a realized P&L is split, all
but the last share are rounded and the last is obtained by **subtraction**, so the
parts always sum to the exact whole — the same technique
`Position._apply_long` already uses for a partial close.

Because contributions are ratios, a partial fill needs no special handling: the
same weights apply to whatever quantity actually executed. A cancelled order
produces no fill and therefore no record.

### `TradeRecord.strategy_id` is removed, not kept alongside

`TradeRecord.strategy_id` is replaced by `contributions`. Keeping a field known to
hold a fabricated value next to the field holding the true one is the failure this
decision exists to end. `AttributionMetrics.pnl_by_strategy` keeps its type
(`Mapping[str, Decimal]`) and changes only its values — from one fictional key to
the real strategies.

### `"ALLOC-NETTED"` is deleted, not redocumented

An earlier draft of this decision kept the sentinel on `OrderRequest.strategy_id`
and merely stopped calling it a strategy. That was wrong, and it was wrong for a
reason worth recording: it is the same "keep a field known to hold a fabricated
value" failure this decision invokes three paragraphs above to justify removing
`TradeRecord.strategy_id`. A release cannot delete the fiction from one public
field on that principle and preserve it in its neighbour.

The value propagates further than the archaeology stated. It is written once, at
`allocation/engine.py:119`, and is then reported by **four** public surfaces:

```
OrderRequest.strategy_id          'ALLOC-NETTED'
oms.Order.strategy_id             'ALLOC-NETTED'   (via oms.views.orders())
ExecutionReport.strategy_id       'ALLOC-NETTED'
OrderBook._by_strategy            keyed on 'ALLOC-NETTED'
  -> orders_for_strategy('ALLOC-NETTED') returns every order the pipeline made
  -> orders_for_strategy('MOMENTUM')     returns nothing, for the strategy that ran
```

So: **the pipeline stops writing a strategy identity it does not have.**
`AllocationEngine.allocate` writes the empty string, `"ALLOC-NETTED"` is deleted
from the codebase, and `OrderBook` does not index an order that declares no
strategy — a falsy guard in `add`, and the **same guard in `remove`**, which
indexes with `self._by_strategy[order.strategy_id]` and would otherwise raise
`KeyError` on an unindexed order.

The empty string, rather than `None`, because this is an identifier on a
serialized and indexed path:

- The repository already uses falsy-as-absent for an optional identifier —
  `Intent.correlation_id: str = ""` — and both `validate_intent` and
  `strategy.validation` already read a falsy `strategy_id` as "no strategy".
- `None` would change the OMS snapshot payload's **value domain**, and
  `OMSSnapshot` carries no `schema_version` (see the correction to ADR-0014
  below). `oms.snapshot._order` decodes with `str(_require(payload, …))`, so an
  older build reading a newer payload would silently produce the literal string
  `"None"`. Changing a payload no version can gate is exactly the silent misread
  ADR-0014 legislated against.
- It keeps `str` on all four types, so nothing widens and no decoder changes.

This differs deliberately from the `| None` chosen for `holding_period_seconds`
and `sector_id`. Those are analytics *values* where absence is a measurement
outcome; this is an *identifier* on a hot, indexed, serialized path where the
repository already has a falsy convention. The alternative — widening all four
types to `str | None` — is more explicit and is defensible, but it costs four
public type changes and forces OMS snapshot versioning into this release.

**Every request writes the same empty value**, including one produced from a
single intent. Populating it in the single-contributor case was rejected: it
would make `orders_for_strategy("MOMENTUM")` return MOMENTUM's un-netted orders
while silently omitting every netted order MOMENTUM contributed to. A partial
answer that looks complete is worse than an empty one. A single-valued index
cannot answer this question for netted orders, and it should not pretend to.

`orders_for_strategy` is **not** deprecated or renamed. It has no production
caller, and it remains correct for a caller using `alphalab.oms` as a standalone
engine (ADR-0009) with strategy ids of their own. Renaming it — and
`Order.strategy_id` with it — would degrade a correct concept to accommodate one
bad producer, and would rename a key inside an unversioned snapshot payload. It
gets a docstring correction stating that an order which declares no strategy is
not indexed, and that attribution lives on `contributions`.

Attribution reads `contributions` or it reads nothing.

### Invariant

```
INV-3  No value that no strategy declared is ever reported through a field named
       strategy_id, or used as a key in the OMS strategy index.
```

## 5. Contributions live in allocation, and do not enter the OMS or the venue

`AllocationState` gains `contributions: PersistentMap[str, tuple[StrategyContribution, ...]]`,
keyed by order id and retired at exactly the points a reservation is released.
That is the index `_trade_record` reads at fill time, which is what lets the
broker return leg — `apply_execution_report(state, order, report)`, which holds
only an OMS order and a report — produce the same record a simulated fill does.

**The OMS does not carry it.** `OrderBook._by_strategy` is a single-valued index
on a hot, complexity-guarded path (`tests/regression/test_oms_book_complexity.py`);
making it multi-valued is a structural change to order lookup for a purpose order
lookup does not have.

**`ExecutionReport` does not carry it.** It is the type a *venue* fill is
converted into. A broker knows nothing about which strategies wanted an order, and
requiring the return leg to supply contributions would be fabrication at the
boundary ADR-0012 built to stop exactly that.

Implementation constraint, because the ordering is load-bearing: `_apply_reports`
builds the trade record inside `_apply_report_to_portfolio` and only then calls
`AllocationEngine.apply_execution`, which may drop the order's ledger entries. The
contributions must be read before that call, and a test must pin it.

## 6. `Position.opened_at`, and what each fill does to it

| Transition | `opened_at` |
| --- | --- |
| open (flat -> non-zero) | **set** to the fill timestamp |
| increase (same sign) | **unchanged** — the position has been held since it opened |
| partial reduction | **unchanged** — the remaining quantity has been held since it opened |
| full close | **ceases to exist**; `apply_fill` pops the position from the map |
| reversal (long -> short, short -> long) | **replaced** with the fill timestamp |
| restored from a snapshot | whatever the snapshot recorded (see 7) |

The reversal row is the one that needs stating. `PortfolioEngine.apply_fill`
emits `PositionReduced` for a reversal — not `PositionClosed` followed by
`PositionOpened` — so a reader of the event log cannot see the reversal without
tracking running quantity and watching for a sign flip. Carrying the old long's
open time onto the new short would be a false number. `opened_at` is therefore
state on `Position`, set by `Position._rebased` at the reversal branches, not
derived from the event log after the fact.

`TradeRecord.holding_period_seconds` becomes `float | None`:
`report.timestamp - opened_at` of the pre-fill position for a fill that reduces or
closes, and **`None`** for a fill that opens or increases — such a fill has held
nothing, and `0.0` is a false number for it. `calculate_trade_metrics` averages
only the records that have one, which turns `avg_holding_period` from a mean of
zeros into the figure it was named for.

## 7. The portfolio snapshot schema bumps to 2 and refuses version 1

`PORTFOLIO_SNAPSHOT_SCHEMA` becomes an explicit `2`. It currently aliases
`DEFAULT_SCHEMA_VERSION`, which is *also* the version of `LifecycleSnapshot`,
`CommonEvent` and `BaseEvent`; bumping the shared constant would version every
event in the system as a side effect of adding one field to a position.

A v1 payload does not record when a position opened, and no honest value can be
invented for it. `last_updated` is the last mark-to-market timestamp — for a
position marked on every event it is effectively *now*, which would report a
holding period of approximately zero for a position held for a year. That is
exactly the false number this release exists to remove.

Migration was therefore rejected, and so was the softer variant of accepting a v1
payload and defaulting `opened_at` to "unknown": it requires
`require_schema_version` to grow a set of accepted versions plus per-version field
handling, which is infrastructure — and ADR-0014 said the field exists "so that the
first schema change is a decision rather than a silent misread". Refusing is the
decision that cannot misread. v2.5.0 shipped one day before this ADR was written;
there is no corpus of v1 snapshots to strand, and the existing refusal message
already says what to do: read it with the build that wrote it.

## 8. Sector attribution is represented as absent, not as a bucket

`TradeRecord.sector_id` becomes `str | None`, the pipeline passes `None`, and
`calculate_attribution` **omits** records with no sector from `pnl_by_sector`. A
pipeline-produced report therefore has `pnl_by_sector == {}` — an empty mapping,
which is the absent number — while a caller who *does* have sector data (as
`benchmarks/benchmark_analytics_engine.py` does) still gets a real breakdown.

`"UNCLASSIFIED"` is not itself a lie; the lie is a `pnl_by_sector` mapping with one
key, which reads as a sector breakdown and is not one. Removing the field outright
was rejected because `TradeRecord` is public and constructible, and a caller with
a security master of their own has a legitimate use for it. No security master is
added, and no sector data is invented.

This is the same shape as the holding-period decision, deliberately: one rule for
"the system does not know this", applied twice.

## 9. Deprecation is sized to blast radius, and `core.events` gets no runtime warning

| Surface | Mechanism | Why |
| --- | --- | --- |
| `alphalab.integrations` | package-level `DeprecationWarning` on import | no production importer; the warning reaches exactly the callers who import it |
| `alphalab.kernel` | package-level `DeprecationWarning` on import | same — importers are the package itself, one test, and the docs |
| `alphalab.core.events` | **documentation and this ADR only** | see below |
| `alphalab.common.CommonEvent` | module `__getattr__` on `alphalab.common`, warning on that one name | fires on use, never on import |

`alphalab.core.__init__` eagerly re-exports thirteen `core.events` symbols at its
line 4. Any import-time warning inside `core/events/__init__.py` therefore fires on
`import alphalab.core` — the canonical core package that the entire execution path
depends on — for every consumer, on every run. A module `__getattr__` cannot help,
because the names are already bound eagerly. A warning that fires on the canonical
package to deprecate a subpackage nothing on the execution path uses is noise that
trains people to filter deprecation warnings, so `core.events` is deprecated in
prose and in this ADR, and warns at runtime in v3.0 or not at all.

`CommonEvent` has zero consumers anywhere — not one call site outside its own
re-export. Removing it from the eager import in `alphalab/common/__init__.py` and
serving it from a module `__getattr__` (PEP 562) warns precisely on use while
`alphalab.common` — which almost everything imports, for `BaseEvent` — stays
silent.

Nothing is removed. Removal of all four is v3.0, recorded in a deprecation table
naming target, replacement and removal release, and guarded by a regression test
asserting each surface still warns, so a deprecation cannot silently lapse.

## 10. The duplicate `ExecutionReceived` is recorded, not collapsed

`alphalab.broker.events.ExecutionReceived` and
`alphalab.brokers.events.ExecutionReceived` carry the same four field names and
types. They are **not** interchangeable:

```
broker :  (event_id, timestamp, execution_id, broker_order_id, fill_quantity, fill_price)
brokers:  (event_id, timestamp, execution_id, broker_order_id, fill_price, fill_quantity)
```

Both are constructed positionally — `broker/paper.py:164` passes
`(fill_qty, fill_price)`, `brokers/manager.py:168` passes
`(fill_price, fill_quantity)`. Collapsing them into one type would silently swap
price and quantity at one of the two call sites and produce a fill that is
structurally valid and economically wrong. "Field-identical" is true of the field
set and false of the constructor.

There is also a genuine ADR-0012 inconsistency to record. ADR-0012 justifies the
duplicated event names by saying `account_id` "is what makes them *routing*
events: the single-broker equivalents in `alphalab.broker.events` have no account
to name." `brokers.ExecutionReceived` has no `account_id`. By ADR-0012's own
stated criterion it is not a routing event, and its existence is unjustified by
the ADR that is supposed to justify it.

v2.6 records the finding and takes no code action. The resolution — either give
`brokers.ExecutionReceived` the `account_id` that would make it a routing event,
or remove it in favour of the canonical one — changes a public event type's shape
and belongs to v3.0.

---

# Consequences

Benefits

- Capital committed to working orders is capital the budget guard can see. The
  reproduction above refuses at the second event instead of reaching 5.4x the
  budget.
- Strategy-level P&L describes strategies. `pnl_by_strategy` stops reporting a
  fabricated key and starts splitting by real contribution, with the parts summing
  exactly to the whole.
- `avg_holding_period` becomes a measurement rather than a mean of zeros.
- `pnl_by_sector` says nothing where the repository knows nothing, instead of
  saying "UNCLASSIFIED".
- A reservation leak that accumulated without bound on any run with adverse
  slippage is closed.
- Four dead surfaces have a stated removal release and a test that keeps the
  notice alive.

Trade-offs / breaking changes

- **`TradeRecord` changes shape.** `strategy_id: str` is replaced by
  `contributions`; `sector_id` and `holding_period_seconds` become optional.
  Positional construction breaks. This follows the precedent of v2.2's
  `release_reservation` signature change and v2.3's `alphalab.brokers` API change:
  a minor release may break a public shape when the alternative is keeping a value
  known to be wrong. No alias is provided, because an alias would keep returning
  the wrong answer under a familiar name.
- **`pnl_by_sector` is empty for pipeline-produced reports.** That is the decision,
  not a regression.
- **`orders_for_strategy` returns nothing for pipeline-produced orders**, where it
  previously returned all of them under `"ALLOC-NETTED"`. Nothing in this
  repository called it; it stays correct for callers who supply their own strategy
  ids. An OMS snapshot written by v2.6 carries `"strategy_id": ""` for pipeline
  orders, which older builds read as an empty string — a value they already
  accept, which is why the empty string was chosen over `None`.
- **v1 portfolio snapshots are unreadable by v2.6.** There is no migration, by
  decision.
- **Allocation can now refuse a batch it previously accepted.** Only when capital
  is already committed — which under `SIMULATED` routing is never, because a
  reservation is consumed or released within the event that created it. Backtest,
  replay and paper are behaviourally unchanged; `EXTERNAL` routing is where this
  bites, and it is what the release is for.
- **Strategies must be prepared to be refused.** A strategy that keeps expressing
  an intent while its earlier orders are still working will see later batches
  rejected rather than silently resized.

---

# Alternatives Considered

**Make `CapitalBudget` consumable.** Rejected: it changes what a target weight
means between events, makes equal weights unequal and order-dependent, applies to
only three of the five sizing models, and cannot be expressed without making order
quantity depend on intent iteration order.

**Let risk incorporate working orders.** Rejected: `RiskState.exposure` and
`buying_power` are recomputed from settled state on every event, so the figure
would be erased and then double-counted on settlement. Two subsystems answering
"how much is committed" is the failure the invariant in decision 2 exists to
prevent.

**Split P&L by absolute contribution.** Rejected: it gives a selling strategy a
positive share of a long position's gain. The bounded weights are not worth the
wrong sign.

**Carry a precomputed weight instead of a signed quantity.** Rejected: it
introduces a second rounding policy at the point of construction, where the
repository's rule is that rounding happens once, at entry, and splits are closed
by subtraction.

**Set `OrderRequest.strategy_id` to the real strategy when there is exactly one.**
Rejected: `orders_for_strategy("MOMENTUM")` would then return MOMENTUM's
un-netted orders and silently omit every netted order it contributed to. A
partial answer that looks complete is worse than an empty one.

**Keep `"ALLOC-NETTED"` and merely stop documenting it as a strategy.** Rejected;
this was an earlier draft of decision 4. It preserves in `OrderRequest` and
`oms.Order` exactly the fabricated value this release removes from `TradeRecord`,
on a principle that cannot apply to one field and not its neighbour.

**Widen `strategy_id` to `str | None` on all four types.** More explicit, and
consistent with the `| None` chosen for `holding_period_seconds` and `sector_id`
— but it changes the OMS snapshot payload's value domain, and `OMSSnapshot` has
no `schema_version` to gate it: `oms.snapshot._order` calls
`str(_require(payload, "strategy_id"))`, so an older build reading a newer
payload yields the literal `"None"`. Rejected as the larger change for the same
guarantee.

**Rename `Order.strategy_id` / `orders_for_strategy` to say "originator".**
Rejected, despite being ADR-0012's own naming-correction precedent: it renames a
key inside an unversioned snapshot payload, keeps the fabricated value alive, and
degrades a concept that is correct for standalone OMS callers in order to
accommodate one bad producer.

**Deprecate `orders_for_strategy`.** Rejected: the query is correct. What was
wrong was what the pipeline wrote into the field it indexes.

**Remove strategy attribution until the system can represent it.** Rejected: the
information exists — `size_intents` has it and throws it away. That is the
opposite of the `sector_id` case, where no security master exists anywhere, and
the two are treated differently for that reason.

**Derive `opened_at` from the portfolio event log.** Rejected: a reversal emits
`PositionReduced`, so the log cannot distinguish it from a partial reduction
without replaying running quantity and watching for a sign change. State is the
direct representation.

**Migrate v1 portfolio snapshots.** Rejected: no truthful value for `opened_at`
exists in a v1 payload, and the machinery to accept multiple versions is
infrastructure built for one field in one release, days after the field it would
migrate was introduced.

**Warn at import on `alphalab.core.events`.** Rejected: `alphalab.core`
re-exports it eagerly, so the warning would fire on the canonical core package for
every consumer on every run.

**Collapse the duplicate `ExecutionReceived`.** Rejected for v2.6: the two
declare their fields in opposite order and are both constructed positionally, so a
collapse silently swaps price and quantity at one call site.

---

# Correction to ADR-0014

ADR-0014 states that "Every snapshot envelope therefore carries `schema_version`".
That is not true and was not true when it was written. `OMSSnapshot` carries
`orders`, `active_orders`, `completed_orders`, `history` and `events`, and no
version field; `from_primitives` does not call `require_schema_version`. Only
`PortfolioSnapshot` and `LifecycleSnapshot` — the two envelopes v2.5 added —
carry one.

The claim is corrected here rather than acted on. Adding a version field to
`OMSSnapshot` would be a third schema change in a release that needs one, for a
state this release does not otherwise touch. It is recorded as an open item, not
scheduled.

---

# Not in scope

Carried forward, unchanged by this ADR: real broker transport, streaming market
data, an async live runtime, reconnect scheduling, order-state polling,
multi-currency valuation, artifact storage, enterprise RBAC enforcement, dataset
provenance, a security master, a universal runtime, lifecycle -> execution
integration, a `schema_version` for `OMSSnapshot`, and the removal of
`integrations`, `kernel`, `core.events` or `CommonEvent`.
