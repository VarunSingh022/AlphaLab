# ADR-0044: The Universal Execution Contract — Capabilities, Lifecycle, Algorithms, Routing and Execution Analytics

## Status

**Accepted and implemented in v3.9.0.**

The ninth capability release on the architecture frozen at v3.0.0. It adds no
package and **no package edge**. It adds modules to `alphalab.core`
(`capabilities`, `lifecycle`), `alphalab.broker` (`lifecycle`, `requests`),
`alphalab.execution` (`algorithms`, `routing`, `quality`) and `alphalab.common`
(`currency`, moved down from `alphalab.allocation.capital`), and extends
`broker.order`, `broker.validation`, `broker.paper`, `broker.reconciliation`,
`oms.order`, `runtime.broker_routing`, `core.contribution`,
`analytics.attribution` and three `alphalab.lifecycle` modules. No snapshot
schema changes, no durable state is added, and every v3.1 through v3.8
invariant holds.

Extends **ADR-0012**: the broker boundary gains a normalized event vocabulary, a
defined meaning for every reported event, identities for cancel and amend
requests, and a reconciliation against a whole venue snapshot — the mirror
stays the mirror, the venue stays the authority, and nothing is added to the
broker snapshot. Amends **ADR-0008**: the order lifecycle's transitions are now
a canonical table in `core`, beside the status vocabulary ADR-0008 put there.

Depends on **ADR-0008** for the canonical execution types, **ADR-0009** for the
execution path, **ADR-0012** for the mirror, the one-to-one `ExternalOrderMap`
and the mirror-to-venue reconciliation, **ADR-0015** for `StrategyContribution`
and the reservation ledger, **ADR-0028** and **ADR-0043** for the FX authority
and the `CurrencyConverter` seam, **ADR-0038** for the cost-model roles and
"absent is not zero", **ADR-0040** for the deployment capability contract and
the book-to-mirror reconciliation, **ADR-0041** for fingerprints, and
**ADR-0042** for the research-settings precedent the lifecycle join follows.

---

# Context

At v3.8 AlphaLab could decide what to own, size it, check its risk and send it
to one venue as one order, and it could reconcile its book against a mirror of
that venue. Between "send it" and "what did it cost" there were five gaps:

**1. No capability model an order could ask.** v3.5's `BrokerCapabilities`
answers whether a broker can run a *deployment*. It is one broker-wide record,
so "supports stop orders" either overstates a broker's options or understates
its equities; it has no account, so a short-sale permission had nowhere to
live; and it is two-valued, so "the broker says no" and "nobody said" read the
same.

**2. No defined meaning for most of what a venue reports.** A fill had one —
`broker.reconciliation.apply_execution`, idempotent in its execution id since
v2.3 — and nothing else did. An acknowledgement, a rejection, a cancel, an
expiry, an amendment, a refused cancel, a position, a balance and a disconnect
were each whatever an adapter's own methods chose to do, so two adapters could
leave the mirror in two states from the same messages. The OMS order's methods
held one set of lifecycle rules in their guards and the broker mirror held a
slightly different set, and the one place they disagreed — a fill arriving
while a cancel is pending — was unreachable in the OMS and routine at a venue.

**3. No way to work an order.** An order went to a venue whole. There was no
schedule, no slicing, no participation and no notion of a child order that
still belongs to its parent and to the strategies that asked for it.

**4. No routing decision.** With several venues, nothing compared what each
quoted, could do and would cost, and nothing recorded why one was chosen.

**5. No measurement of execution.** v3.3's `ExecutionCosts` itemize what a cost
model *assumed*. Nothing measured what an order *achieved* against its decision
price, its arrival, the market's VWAP or its limit, how long each step took, or
how often a venue refused.

Beneath all five, cancels and amendments had no identity, so a retried cancel
and a second cancel were indistinguishable, and the only mirror-to-venue
reconciliation compared loose remote records with no notion of when the venue
produced them.

---

# Decision

## 1. Placement: canonical contracts in `core`, semantics in `execution`, the boundary in `broker`

| Concern | Home | Why there |
| --- | --- | --- |
| What a broker can do, and what an order needs | `core.capabilities` | read by the broker boundary, routing, the runtime's gate and the lifecycle's portability check — only `core` is below all of them (ADR-0008's rule) |
| The order lifecycle's legal transitions | `core.lifecycle` | read by the OMS and the venue boundary, which sit on opposite sides of the graph |
| A venue's reported events, applied to the mirror | `broker.lifecycle` | the mirror's owner (ADR-0012) |
| Cancel and amend requests, and their ledger | `broker.requests` | requests *to* a venue, addressed by venue handle |
| Snapshot reconciliation, mirror against venue | `broker.reconciliation` | beside `reconcile`: the same pair, richer evidence |
| Execution algorithms | `execution.algorithms` | how an `OrderRequest` is worked; reads only `core` |
| Route selection | `execution.routing` | reads the capability model and the v3.3 cost models |
| Execution quality | `execution.quality` | reads the canonical `OrderRequest` and `ExecutionReport` |
| Sending a child for its parent; binding children to parents | `runtime.broker_routing` | where `route_order` already sends parents |
| Projection to the v3.5 record; children in book-to-mirror reconciliation; execution identities in a fingerprint | `lifecycle.specification`, `lifecycle.reconciliation`, `lifecycle.fingerprint` | the lifecycle's existing joins |
| The currency-conversion protocols | `common.currency` | `execution` may import neither `allocation` nor `portfolio`; moved unchanged and re-exported, so every v3.8 import resolves to the same objects |

Every import these modules make follows an edge that already existed;
`test_v39_invariants.py` pins the package set and every edge, and the graph has
no cycle. `broker` re-exports the capability model and `ExecutionEventKind`, so
an adapter author imports from the package they already use.

## 2. Capabilities: declared where true, three-valued, checked at every level

A `CapabilityDeclaration` states an adapter's answers at three levels:

* **venue** — `STREAMING` and `CANCEL_REPLACE`, the connection as a whole;
* **market** — per asset class on one listing venue or on every venue of the
  class (`ANY_LISTING_VENUE`): order types, times-in-force, short sales,
  fractional quantities, extended hours and bracket orders;
* **account** — the asset classes an account may trade, margin and short sales.

Every answer is a `Support`: `SUPPORTED`, `UNSUPPORTED` or `UNDECLARED`, and
`UNDECLARED` is also the answer for anything not mentioned. A market's order-type
list is complete, so an order type missing from a declared market is
`UNSUPPORTED`; an asset class declared as not offered is `UNSUPPORTED`; a market
never declared is `UNDECLARED`. A declaration that contradicts itself — a
market-level capability declared for the connection, one market declared twice,
an asset class declared both offered and withdrawn, an account twice — is
refused at construction.

`order_requirements` derives what one order needs, and derives the two things
the order itself decides — a short sale (a sale beyond the position held) and a
fractional quantity — so a caller cannot restate them wrongly. Everything else
is a keyword with no default. `check_compatibility` checks every requirement at
every level that decides it — a short sale at the market *and* the account —
and returns a `CompatibilityReport` of `CapabilityCheck`s, each with its
dimension (`MARKET`, `ORDER_TYPE`, `TIME_IN_FORCE`, `EXECUTION_FEATURE`,
`ACCOUNT`, `RISK`), level, answer and reason. **`COMPATIBLE` only when every
check is `SUPPORTED`**; any `UNSUPPORTED` makes it `INCOMPATIBLE`; `UNDECLARED`
with nothing unsupported makes it `UNDETERMINED` — never compatible. That is
v3.5's "a missing observation is not a healthy one" and v3.6's `NOT_VERIFIED`,
applied to a single order. `supports` answers one capability of the flat
thirteen-member vocabulary for a stated scope and refuses a question whose scope
is missing.

AlphaLab discovers nothing: an application that has an adapter knows what its
venue supports and says so. `adapter_id` is an opaque label. Declarations,
requirements and reports have identities (`alphalab.capability_declaration.v1`,
`alphalab.execution_requirements.v1`, `alphalab.compatibility_report.v1`);
markets and accounts are held sorted, so listing order never changes one.

**Declared once.** `lifecycle.broker_capabilities_from` projects a declaration
onto the v3.5 `BrokerCapabilities` for the markets a deployment trades:
order types and times-in-force are the intersection across those markets, short
selling needs every market and the account, and anything the record must state
that the declaration left `UNDECLARED` is refused with the gap named — the
two-valued record cannot say "unknown", and projecting silence either way would
invent an answer. The v3.5 specification and v3.6 portability checks therefore
read the same declaration routing and the runtime read.

## 3. One transition table, read by the OMS and the venue boundary

`core.lifecycle` states twelve `ExecutionEventKind`s — eight about an order
(`ORDER_ACCEPTED`, `ORDER_REJECTED`, `ORDER_PARTIALLY_FILLED`, `ORDER_FILLED`,
`ORDER_CANCELLED`, `ORDER_EXPIRED`, `ORDER_REPLACED`, `ORDER_CANCEL_REJECTED`),
two about the account (`POSITION_CHANGED`, `BALANCE_CHANGED`) and two about the
connection (`BROKER_DISCONNECTED`, `BROKER_CONNECTED`) — and `ORDER_TRANSITIONS`,
for every status the events that may leave it and where each lands. Intent,
request, state and event are kept apart: a reported event is evidence about
state, and applying it is a decision the table makes.

`oms.order.Order`'s methods ask `next_order_status` whether their move is legal,
keeping every refusal message they had; `broker.reconciliation.apply_execution`
and `broker.lifecycle` read the same table. `classify_order_event` is total over
the status events and every status and decides, in order: a legal
**transition**; a **duplicate** (the order already holds the reported status); a
**stale** report (the order has moved past it — the current status is reachable
from the reported one); otherwise a **conflict**. Terminal statuses are
absorbing. What the table deliberately does not contain: idempotency (decided
from its shape), requests (entering `CANCEL_PENDING` is *AlphaLab asking*, so it
is `CANCEL_REQUESTABLE_STATUSES`, not an event row) and broker-local statuses,
which stay in `broker.order` and are read as the canonical status they behave
as (`BROKER_LOCAL_EQUIVALENTS`: `PENDING_SUBMIT` as `NEW`, `SUBMITTED` as
`PENDING`, `PENDING_CANCEL` as `CANCEL_PENDING`) — each one of the statuses
`BROKER_STATUS_EQUIVALENTS`, moved from `lifecycle.reconciliation` to
`broker.order` and re-exported as the same object, already calls consistent.

Two decisions are recorded in the table. **A fill while a cancel is pending
lands, and the cancel stays pending**: the venue traded first, so the fill is
real; a fill that leaves quantity working leaves the order `CANCEL_PENDING`,
because the cancel is still in flight and a status that forgot it would invite a
second one. **A refused cancel returns the order to the working status its fills
imply**, which is why `next_order_status` takes a filled quantity — the only use
it makes of one, besides refusing a rejection of an order that has traded.

## 4. Every venue report gets exactly one outcome

An adapter translates its venue's messages into `VenueEvent`s; a payload is
validated for its kind. `broker.lifecycle.apply_venue_event` returns the new
mirror and a `LifecycleDecision` whose outcome is `APPLIED`, `DUPLICATE`,
`STALE`, `CONFLICT`, `UNKNOWN_ORDER` or `INVALID`. Anything but `APPLIED`
returns the mirror unchanged, so applying an event twice is safe and a refused
event can be retried after a reconciliation. `is_break` is true for the last
three; redelivery and reordering are routine, contradictions are not.

* **Fills** go through `classify_execution` / `apply_execution`, unchanged in
  meaning: idempotent in `execution_id`, a fill on a terminal order or an
  overfill a conflict. A fill for an order still awaiting its acknowledgement is
  applied, and the decision records the `ORDER_ACCEPTED` it implies; the late
  acknowledgement is `STALE`.
* **The quantities decide, not the name.** `ORDER_FILLED` means the fill left
  nothing working *as the venue saw it*; delivered ahead of an earlier partial
  fill it leaves quantity working in the mirror. Refusing it would break the
  additivity that makes out-of-order fills converge, so it is applied and the
  disagreement is recorded in the decision's reason. A missing fill shows up as
  a filled-quantity difference in a snapshot reconciliation, where it belongs.
* **Amendments, positions and balances are absolute and not ordered here.** A
  `BrokerOrder` has no venue sequence field, and adding one would change
  `BROKER_SNAPSHOT_SCHEMA`. Delivering them in venue order is the adapter's
  stated obligation; a snapshot reconciliation is the check.
* **Connectivity**: `BROKER_CONNECTED` sets the connection and
  `resync_required` — ADR-0012's "has not confirmed what the venue already
  holds".

Nothing is appended to `BrokerState.events`: that log is the adapter's record
of what it emitted and its snapshot reads a closed set of event types. The
decisions are the caller's to keep, exactly as `apply_execution` has always left
them.

## 5. Requests have identities; a retry is not a second request

`CancelRequest(broker_order_id, attempt, requested_at)` and
`ModifyRequest(broker_order_id, revision, quantity, price, requested_at)` are
identified by content and place in a sequence, never by send time
(`alphalab.venue_request.v1`). `issue_cancel` / `issue_modify` consult a
caller-held `RequestLedger` and answer `NEW` (send it), `DUPLICATE` (a retry, or
a cancel already in flight) or `REFUSED` (unknown or finished order, an
amendment that changes nothing or leaves nothing working, a number that reuses
or predates one issued). A new cancel moves the mirror to `PENDING_CANCEL`; a new
amendment changes nothing until the venue's `ORDER_REPLACED` confirms it. Losing
the ledger across a restart is safe in the direction that matters: both
operations are absolute at a venue, and a mirror still `PENDING_CANCEL` answers
`DUPLICATE` on its own evidence.

## 6. Execution algorithms: stated completely, deterministic, children that stay the parent's

`TWAP`, `VWAP`, `Participation`, `Slicing` and `Iceberg` each state an
objective, inputs, sizing, timing and completion rule in their docstrings, and
nothing else changes their output. Time is supplied (`now`), volume is supplied
and attributed (`VolumeProfile`, `IntervalVolume`), and a missing volume is
`None`, never zero.

The two schedule algorithms are one construction: a clock `x` (elapsed time, or
cumulative *expected* volume) and the trajectory
`F(x) = 1 − sinh(κ(1 − x)) / sinh(κ)` — the shape of the Almgren–Chriss
liquidation trajectory, a straight line at `κ = 0`. `κ` is stated by the caller
(`Urgency`, bounded at `MAX_URGENCY`); AlphaLab neither estimates it nor claims a
schedule optimal. `sinh` is evaluated in `Decimal` at 34 digits, so a schedule is
identical on every platform. Each slice's ideal share is converted to whole
multiples of a stated `quantity_increment` by the **largest remainder method**,
ties to the earlier slice: the slices sum to the parent exactly and none strays
a whole increment from its ideal. An incomplete VWAP profile is refused, or —
under `TIME_WEIGHTED`, chosen — the whole schedule is planned on time and says
so; never a patchwork of volume and time shares.

`AlgorithmState` is a value. **Every release tops up to the trajectory** — the
target less what has filled and what is working — so a child that expired
unfilled is caught up by the next release and nothing is committed ahead of the
schedule. `Participation` sizes from observed volume only (own fills included,
since a tape cannot tell them apart), withholds a child below its minimum,
caps one above its maximum, and at the window's end leaves the rest unfilled or,
chosen, completes it above the rate. `Iceberg` works one visible tranche and
keeps the rest inside AlphaLab; it emulates no venue's native reserve order. A
fill still counts after a cancel, and a rejected child fails the run — the
algorithm does not retry a venue's refusal.

**Children roll up to the parent.** A `ChildOrder` carries its parent's
`strategy_id` and every `StrategyContribution` unchanged, and the
`algorithm_id` of the run that released it. The parent OMS order keeps the
reservation and the contributions; `runtime.route_child_order` sends a child as
a venue order *for the parent* (its `oms_order_id` is the parent's; its handle is
`child_broker_order_id(parent, sequence)`), and its fills settle on the parent
through `apply_broker_execution` — the canonical path a simulated fill takes.
The many-to-one relation lives in a caller-held `ChildOrderBindings`, rebuildable
from the mirror with `from_mirror`, so the one-to-one `ExternalOrderMap`
(ADR-0012) is untouched. The gates, in order: connected; the parent not already
at the venue whole; the child the parent's (order, asset, side), the parent
still working, and the child not putting more at the venue than the parent has
unfilled; not already bound; and a supplied capability report `COMPATIBLE`.
`route_order` gains the same capability gate as a keyword-only argument whose
default checks nothing, so every v3.8 call is unchanged.

Configuration, run and schedule identities: `alphalab.execution_algorithm.v1`,
`alphalab.execution_algorithm_run.v1`, `alphalab.execution_schedule.v1`.

## 7. Route selection: a function of the evidence, explained

`select_route(request, quotes, profiles, policy)` compares the venues a caller
supplies — a `VenueQuote` (what it showed, when, in what currency, from where)
and a `VenueProfile` (its `CapabilityDeclaration`, its v3.3 `ExecutionCostModel`,
its latency or `None`) — under a `RoutingPolicy` (objective, maximum quote age,
optional latency cap, split, partial and excluded venues). There is no
discovery, no clock and no configuration outside the policy.

A venue's expected fill is priced by its own cost model from its quote
midpoint, so the spread is counted once. Every venue becomes a `RouteCandidate`:
`EXCLUDED`; `INSUFFICIENT_EVIDENCE` when a quote, a side, a size, a fresh enough
timestamp (a quote dated after the decision is a look-ahead), a latency the cap
needs, a cost the model could compute, or a declared capability is missing;
`INELIGIBLE` when the venue positively cannot take the order (an unsupported
capability, another currency — nothing converts — a touch outside the limit, no
size, latency over the cap); otherwise `ELIGIBLE`. Eligible venues are ranked by
the quoted touch or the **all-in** price per unit, then lower latency with
unknown last, then venue identifier — an order that is part of the policy's
identity. A single-venue route takes the best-ranked venue that can take the
whole order; a split sweeps the ranking and re-prices each leg at the quantity it
takes — greedy, which is optimal while costs are linear in quantity and is not
claimed optimal otherwise. The decision is `ROUTED`, `PARTIAL` (only if
allowed), `INFEASIBLE` when every venue was positively ruled out, or
`INSUFFICIENT_EVIDENCE` when any could not be assessed — a route that might
exist is not reported as one that does not. `explanation()` names every venue
and why. Policy and decision identities (`alphalab.routing_policy.v1`,
`alphalab.route_decision.v1`) do not depend on the order venues are listed in.

Selection is AlphaLab's; sending is the runtime's; connecting is an adapter's.
Whether an order may be routed away from a venue at all — market rules, a
best-execution obligation — is not something AlphaLab knows.

## 8. Execution quality: measured against named references, nothing fabricated

`OrderExecution` joins the canonical `OrderRequest`, its canonical
`ExecutionReport` fills (each in the execution currency, none repeated, none
beyond the order), the outcome, `ExecutionBenchmarks` (decision, arrival,
interval VWAP, end price, and their source), the limit, an `OrderTimeline` and
the quote midpoints at each fill.

* **Implementation shortfall** (Perold) against the decision price — supplied,
  or the price allocation sized the order at, and said which: execution cost
  split exactly into delay (decision → arrival) and trading (arrival → fills),
  explicit costs from the reports' `commission`, and opportunity cost on the
  unfilled quantity at the end price — `None`, and the total with it, when no end
  price is supplied. Signs are costs for a buy and a sale alike. The total is
  divided between the strategies that asked for the order by
  `core.split_by_contribution`, the v2.6 rule `split_realized_pnl` uses, moved to
  `core` so both call one implementation; shares sum to the total exactly.
* **Slippage** against one named reference — a measurement, kept apart from
  `ExecutionReport.slippage`, the concession a cost model *assumed*.
* **Fill quality**: completeness, improvement on the limit, effective spread
  against each fill's midpoint, fragmentation.
* **Latency** between two named lifecycle marks, saying whose clock stamped each
  and flagging a measurement across two clocks; an end before its start is
  reported inconsistent, never negative.
* **Rejection rate**: venue rejections over *resolved* submissions in a stated
  window; an order AlphaLab refused before sending was never submitted.
* **Venue quality**: what a venue filled, how far from a reference, how often it
  refused and how fast it answered — measured, never substituted for routing's
  expectation.

`execution_quality_report` totals per currency always, and in one reporting
currency only through a supplied `CurrencyConverter` at a stated instant, every
conversion recorded; `FxRates` refuses a missing, stale or future-dated rate
and the report refuses to be totalled without one
(`alphalab.execution_quality.v1`).

## 9. Two reconciliations, two depths of evidence

`broker.reconcile_snapshot` compares the mirror against a `VenueSnapshot` — the
venue's orders, fills, positions and account *as of* an instant, with an absent
section (`None`) kept apart from an empty one. **Freshness first**: a snapshot
dated after the evaluation instant, older than a stated age, or taken before the
mirror's newest change is not compared, and the result says which. Then every
order (quantity, price, filled quantity, status — a broker-local status
consistent with any canonical status it is related to), every fill, every
position in both directions, every balance and the account's identity and
currency, each divergence reported separately in thirteen kinds, duplicated
evidence reported rather than chosen between. `reconcile` refuses the two inputs
its report cannot describe — duplicate remote records and an account in another
currency — rather than collapsing or subtracting them.

`lifecycle.reconcile_execution_state` — book against mirror — gains
`children=`: with the bindings, each child is expected at the venue, joined to
its parent, held to the parent's instrument and remaining quantity, and a
finished parent with a live child is a break; without them nothing changes. Its
parameters, and `reconcile`'s, stay pinned in `test_shared_names_stay_distinct.py`.

## 10. Lifecycle integration without changing a key

`research_configuration_with_execution` writes `execution.<name>.algorithm` and
`routing.<name>.policy` into the research-settings section, so
`canonical_fingerprint_key` is unchanged, every earlier fingerprint verifies,
and a strategy researched with its orders worked or routed a particular way
changes identity when that configuration does. A capability declaration is
deliberately not a component: what a venue can do belongs to the environment a
strategy is deployed to, which the specification and portability checks read.

## 11. Performance is a property of the algorithm, pinned by ratio

`test_v39_complexity.py` asserts growth ratios — capability checks against many
accounts, a venue event stream over many orders, a many-slice schedule, slicing
by count, route selection over many venues, a many-order snapshot
reconciliation and venue quality over many venues — with the stabilized method
of `test_lifecycle_registry_complexity.py`, imported rather than copied. Each
guard was run against the quadratic implementation it guards, put back into a
copy of the package one textual change at a time, and failed (10.1× to 16.0×
for a 4× input, against an 8× bound); three workloads were reshaped until the
quadratic term dominated, and the bound did not move. The guards found one
defect in the v3.9 draft: a declaration's identity was rendered on every check,
making a run of checks quadratic in the declaration (16.0×); it is computed once.

---

# Consequences

**No durable state, no store, no schema.** Every v3.9 value is frozen and
produced by a pure function; `ChildOrderBindings` and `RequestLedger` are values
the caller holds, like `ExternalOrderMap`; nothing enters a snapshot. Every
identity is identical across hash seeds and working directories, checked in
fresh interpreters; no v3.9 module reads a clock, entropy or the environment,
uses a platform-dependent transcendental, or computes in the caller's decimal
context.

**Classification of the scope**

| Capability | Status |
| --- | --- |
| Capability declarations at venue, market and account level; three-valued compatibility; the projection to the v3.5 record | IMPLEMENTED |
| The canonical transition table read by the OMS and the venue boundary; normalized venue events with one outcome each; request identities; snapshot reconciliation | IMPLEMENTED |
| TWAP, VWAP, participation, slicing, iceberg-like; urgency; children that roll up to the parent with its contributions | IMPLEMENTED |
| Route selection with explanations, split and partial routes, evidence and policy identities | IMPLEMENTED |
| Implementation shortfall, slippage, fill quality, latency, rejection rate, venue quality, multi-currency reports; execution identities in fingerprints | IMPLEMENTED |
| A venue sequence number on orders (and ordering of amendments, positions and balances by it); persisting `ChildOrderBindings` and `RequestLedger` in a snapshot; a book-to-mirror reconciliation across several brokers' accounts; an optimal (non-greedy) split under fixed fees or non-linear impact; estimating urgency from risk aversion, volatility and impact; randomized iceberg tranches; implementation shortfall with an intraday market-impact model | DEFERRED |
| Venue connectivity and vendor protocols; capability declarations for real venues; quotes, volume profiles, tape volume and benchmark prices; FX rates; the mapping from adapter and account labels to credentials | EXTERNAL |
| Broker-specific SDKs, APIs, credentials, OAuth or vendor request and response structures in AlphaLab; RedDesk or iluvtrade product logic, marketplace or licensing logic; user identity; LLM dependencies; network-dependent routing or analytics; claims about best-execution compliance | NON-GOAL |

**Known limitations**

- **Amendments, positions and balances are not sequence-ordered** at the venue
  boundary; two delivered out of order leave the earlier value in place until a
  snapshot reconciliation finds it. Delivering them in order is the adapter's
  obligation.
- **`reconcile_execution_state` compares a pipeline's book with one broker
  mirror**, as it has since v3.5; a book spread across several brokers'
  accounts has no aggregate book-to-mirror reconciliation.
- **The split is greedy**: optimal while costs are linear in quantity, not
  under per-trade fees or non-linear impact; each leg's own price shows what it
  costs.
- **The projection refuses what is undeclared**; a declaration used for a
  deployment must state every answer the v3.5 record needs.
- **`ChildOrderBindings` and `RequestLedger` are not persisted**; bindings are
  rebuildable from the mirror, and a lost ledger is safe (§5).
- **VWAP assumes volume is uniform within an interval** of its profile when a
  window covers one partly.
- **Participation's reference volume includes AlphaLab's own fills**, which a
  tape cannot separate.
- **No iceberg randomization and no native reserve-order emulation.**
- **Urgency is stated, not estimated.**
- **Measured amounts are unrounded analytics**, and strategy shares are carried
  to `0.0001`; they are not ledger entries.
- **A child's venue fills are booked on the parent in the order the caller
  applies them**; the algorithm state and the book are kept in step by the
  caller, as the integration test and example 65 do.

**What this release does not add:** no named broker SDK, API or credential —
an adapter is an application's, and AlphaLab's side of it is the normalized
contract; no OAuth, API key, token or vendor message structure; no RedDesk or
iluvtrade logic; no marketplace, licensing or payment logic; no user identity;
no LLM dependency; no network-dependent routing or analytics; no default
urgency, quote age or tolerance, and no benchmark price AlphaLab supplies itself
— the one fallback, a missing decision price read as the price allocation sized
the order at, is named in the measurement's assumptions. The market-data vendor
clients under
`alphalab.marketdata` and the generic REST venue transport of ADR-0031 predate
this release and are untouched by it.
