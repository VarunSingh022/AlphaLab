# ADR-0061: Per-Venue Execution, Plans With Queues, and Child Fills

## Status

**Accepted** (2026-11-17). It is the record the v4.3.0 plan needs before M8
(plan sections J and T; gaps GAP-17, GAP-44 and GAP-45; FUT-045; workstreams
WS-26, WS-11 and WS-10). The owner confirmed FUT-045 in M8 (D-F45) and delegated
the programme's other decisions within the charter's principles on 2026-11-10
(ADR-0056); this record is that decision for venues, composed drivers and child
fills.

It adds one result type to the family AC-08 describes and one optional strategy
protocol (AC-35). It changes nothing for a run that declares none of them, and
reverses no accepted ADR.

---

# Context

**One execution model per run (FUT-045, GAP-17).** `ExecutionPipelineConfig`
holds one `simulator`: one cost model and one latency model price every
simulated order. A strategy that trades an instrument listed on two venues,
with different fees, spreads and latency, cannot be measured on either, nor on
a routing between them. `execution.routing.select_route` chooses venues from
supplied evidence for live and paper trading; no historical run can route.

**A plan and a queue are refused together (GAP-44).** `queued_run` refuses a
queue model when an execution plan works the run's orders, so a TWAP's resting
limit children fill as if they were first in line at their price.

**A child fill reaches the algorithm book, not the strategy (GAP-45).** A
strategy whose parent order is worked in children learns nothing until the
parent's own lifecycle reaches it.

FUT-045's ledger criteria, verbatim: "a run declaring an execution model per
listing venue, each described in the configuration; an order filled by the
model of the venue it was routed to; a run declaring one model unchanged, its
identity unmoved."

# Decision

## 1. Per-venue execution models

- **Declared per venue.** `ExecutionPipelineConfig.venues` maps a venue code to
  a `VenueExecution`, which holds that venue's `ExecutionSimulator`: its costs
  (fees, spread, slippage, impact, tax) and its latency. It is empty by default,
  and an empty map is today's run: one simulator, unchanged.
- **Every simulated order is routed to one declared venue**, by the first of:
  1. the venue its `OrderTerms` names, in a new optional `venue` field, written
     only when set;
  2. the run's declared router, `ExecutionPipelineConfig.router`. A router is a
     deterministic function of the order and the declared venues. The first
     one is `CheapestVenue`, which prices the order at each venue by that
     venue's cost model at the deciding event's price, and picks the cheapest,
     ties to the first venue by code;
  3. the instrument's listing venue, when it is declared among `venues`.

  An order none of these routes is refused, the venue it lacks named. It is
  not filled by a model nobody chose.
- **The routed venue's model fills it.** Its simulator prices the fill and
  stamps it, and its execution report and trade record carry the venue, so a
  run's costs reconcile per venue.
- **Every routing decision is recorded:** the order, the venue, how it was
  chosen (terms, router or listing) and when. The record is pipeline state,
  persisted and restored with the run.
- **Plan children and queues are per venue.** A child is routed as its parent
  was. A queue model keeps its price levels per venue.
- **Crypto venue specifications become models.** A builder turns each venue in
  `crypto`'s specifications into a `VenueExecution` with that venue's fees.
- **Described in the configuration.** `execution_assumptions` describes each
  declared venue's model and the router. `settings()` carries them only when
  declared, so a one-model run is identified as before. Live best-execution
  claims stay out of scope (BDY-026): a router here chooses among declared
  models, nothing more.

## 2. Plans with queues

- **The refusal is lifted.** Under a queue model, a plan's limit children that
  rest join the queue book as any resting limit order does: from the event
  they were placed at, and filling only as far as the model says has reached
  the front of their level. Market children do not rest, and are unaffected.
- **One result holds both books.** `BacktestEngine.run(execution=plan,
  queue=model)` returns `AlgorithmicQueuedResult`, a `BacktestResult` holding
  the algorithm book and the queue book. A run with only one of them keeps its
  result type. The replay driver does the same.
- **Pause and resume persist both books**, and a resumed run is byte-identical
  to an uninterrupted one.

## 3. Child fills reach the strategy

- **An optional protocol.** `ChildFillStrategyProtocol` has one hook,
  `on_child_fill(context, fill)`, which receives a `ChildFill` when it settles,
  at the event it happened. Intents it returns route as any strategy's do.
- **Dispatched only to implementers,** by attribute, as
  `SliceStrategyProtocol` and `ObservationStrategyProtocol` are. A strategy
  without the hook is unchanged, and `StrategyProtocol` itself is unchanged, as
  its manifest entry holds.
- **The pattern, codified.** A new strategy capability is an optional protocol
  with its own ADR, dispatched only to implementers. It is never a new method on
  `StrategyProtocol`. The strategy package's documentation states the pattern.

## 4. What is persisted

The venues, the router and the routing record join ADR-0060's fields in the
pipeline snapshot's one schema step, 8 to 9. They are written only when a run
declares one of them, so a run declaring none writes version 8, byte for byte.
A composed result's two books are captured by the payloads each already has.

# Settled at implementation (2026-11-21)

1. **Plans with queues.**
   - **The refusal is lifted, and only it.** `queued_run` refuses a queue model
     only on a run that routes `EXTERNAL`. Under a plan the queue book holds
     the plan's children, never the run's own orders: those are the parents,
     which the plan works and which never rest. `RunEngine.advance` still
     refuses a queue book under `ALGORITHMIC` routing, so the drivers keep the
     book beside the children's work.
   - **Which children.** A limit child, which can rest. It joins its level from
     the event it was released at, as a resting limit order joins at the event
     it was placed on: after its taking turn under `SAME_EVENT`, when that
     leaves it resting, and at once otherwise. A child released at an event
     that shows no quote, a print, starts from an unknown queue, as an order
     placed there does. Market children never rest and are not governed.
   - **Each turn.** Before a resting child's turn the model advances its place
     from what the event showed. The reading is the pipeline's own
     (`_queue_observation`), one for children and orders.
     `ExecutionPipeline.work_child(queue_turn=)` then fills nothing until the
     model puts the child at the front, and no more than the model allows, the
     fill policy deciding within that. A child's taking turn is not governed.
   - **The book keeps only working children.** After each event's work the
     queue book keeps a place for each limit child still working: an expired
     slice, or a filled or cancelled child, leaves it. A paused replay keeps
     the same, and so does the end of the data.
   - **One result.** `AlgorithmicQueuedResult` is an `AlgorithmicResult` that
     holds the queue book too, its execution assumptions naming the model, and
     a `ReplayResult` reads either kind's queue. It is a new class rather than
     a field, so every existing result keeps its shape.
   - **Persistence.** A paused replay already carried each book in its own
     optional field, so no schema moves: the payload holds both.
   - **Tests.** `tests/integration/test_plans_with_queues.py` holds
     hand-calculated fills from the queue suite's records:
     - a one-slice TWAP's limit child fills 20, 50 and 30 as the queue clears,
       where the plan alone fills 100 at the first print: the fills of the same
       order resting on its own;
     - a child short of the front keeps its place, 50 behind;
     - a two-slice TWAP's expired child leaves the book, and the next, placed
       at a print, starts from an unknown queue;
     - market children, and the run's record, unchanged.

     They also cover the result's books and assumptions, the refusal, the
     replay equal to the backtest, and a replay paused at each boundary
     resuming to the same books and result.
2. **Child fills reach the strategy.**
   - **The event and its topic.** `ChildFillReceived` lives in the strategy
     layer beside `FillEvent`, which it mirrors: the child, the parent order,
     the instrument, the whole fill and its price, the parent's side, the
     strategy's signed share and the execution id the parent's report records.
     The backtesting layer's `ChildFill` cannot be the event, since the
     strategy layer imports nothing above it. It has its own topic,
     `child_fills`, as slices and observations have theirs, so a strategy
     subscribed to `*` that defines the hook hears it.
   - **Who hears it.** Each strategy that contributed to the parent, divided
     by the contributions the fill's trade record holds -- the parent's own
     leave the ledger once it is filled -- as a `FillEvent` is. Only a running
     strategy subscribed to `child_fills` whose class defines `on_child_fill`
     of its own (`defines_on_child_fill`; `BaseStrategy`'s answers nothing).
     A run none of whose strategies would hear one builds no context and is
     the run it was.
   - **When.** At the event the children filled at, after the plan's work
     there: `ExecutionPipeline.deliver_child_fills`, which the algorithmic
     driver calls with each child's id and the report it settled through.
     `algorithmic.work` takes the context factory as a keyword, and both
     historical drivers give it.
   - **What an answer is.** An intent like any other: cancels and amends
     first, then allocation, risk and the OMS, resting until its asset's next
     event, as what a strategy asks for from a fill does. Under a plan a new
     order is a parent, which the plan adopts at that event. Exercise
     instructions and elections take effect at the instant, as a step's do.
   - **The pattern.** `ChildFillStrategyProtocol`'s docstring, the strategy
     package's and `docs/COMPOSING_STRATEGIES.md` state it: a capability added
     after the ten hooks is an optional protocol with its own ADR, dispatched
     only to implementers, never a new method on `StrategyProtocol`.
   - **Tests.** `tests/integration/test_child_fill_feedback.py`: under the
     queue suite's records a strategy hears its child's fills of 20, 50 and 30,
     each its whole share, a buy, by the parent's execution ids, and without
     the queue the one fill of 100. An answer of a sale of 10 is placed at the
     fill it answers, adopted and filled at 99.985 two records later, and its
     child's fill is heard with a share of -10. A strategy without the hook, or
     not subscribed, hears nothing and fills as the plain run does. A replay
     hears what the backtest hears, and a replay paused at each boundary
     resumes to the same result.
3. **Per-venue execution models.**
   - **Where they live.** `VenueExecution`, `VenueRouter` and `CheapestVenue`
     are in `alphalab.execution.venue_models`, exported from
     `alphalab.execution`. `ExecutionPipelineConfig.venues` is held sorted by
     code, read-only; `router` requires venues.
   - **Routing, before acceptance.** Under `SIMULATED` and `ALGORITHMIC`
     routing, with venues declared, each request is routed after its terms
     are judged and before risk: the venue its `OrderTerms.venue` names, the
     router, then its instrument's listing exchange when one is declared under
     that code. A request none of them routes is refused like any unworkable
     request -- dropped, its reservation freed, the strategy told why -- and
     never becomes an order. A package's legs are routed the same way, and a
     leg no venue routes refuses the package. Under `EXTERNAL` routing the
     venue is the live route's, and nothing is routed here.
   - **What the router sees.** A function pricing the order at a venue: that
     venue's cost model, for the order's whole quantity, at the deciding
     event's price and its quote and size when the event is the order's
     asset's. `CheapestVenue` takes the lowest, ties to the first code.
   - **The routing record is metadata.** Each routed order carries `venue`,
     `venue_by` (`terms`, `router` or `listing`) and `venue_at`, as
     ADR-0060's and ADR-0062's state does. Every snapshot carries it, and a
     run's record keeps its form, so no OMS schema moves.
   - **The venue's model fills it.** Its simulator prices the fill, stamps it
     -- its latency, under either latency pricing -- and judges a limit
     order's taking price. The execution report's `venue` is the routed one.
     A plan's child fills on its parent's venue. Queue places are each
     order's or child's own, so they are per venue already.
   - **`OrderTerms.venue`.** Written only when set, through the terms' own
     serialization, so every earlier order's terms keep their bytes, and read
     back optionally. Terms naming different venues are different terms, and
     do not net.
   - **Crypto venues.** `alphalab.crypto.crypto_venue_execution` builds a
     venue's model from a `VenueSpecification`: its taker fee as the
     commission and its maker fee as the resting fill's, with the spread,
     slippage and latency required. It lives in the crypto package, which now
     depends on the execution package, so the execution package knows nothing
     of crypto.
   - **What is recorded.** The configuration record writes each venue's code
     and its simulator's type and description, and the router's, only when
     declared, at version 9. `RuntimeObjects` gains `venues` and `router`,
     supplied back on restore and held to what was recorded.
     `execution_assumptions` appends the venues and the router to the costs'
     description only when declared, and with venues declared `frictionless`
     reads their models, since the run's own simulator then prices no order.
   - **The ledger.** FUT-045 is implemented, its completion criteria met:
     each venue's model described in the configuration, an order filled by
     the model of the venue it was routed to, and a one-model run unchanged.
     It leaves ROADMAP's Future work.
   - **Tests.** `tests/regression/test_per_venue_execution.py` holds
     hand-calculated fills:
     - the cheapest venue chosen, ten cents and one second at ALPHA;
     - a named venue, twenty cents and two seconds at BETA;
     - a tie to the first code, and the listing venue without a router;
     - both refusals, and a run declaring no venue unchanged, at version 8;
     - costs reconciling per venue (GR-4's rule);
     - the description, and the configuration's refusals;
     - a resume at version 9, refused without its venues, with one configured
       otherwise, or without its router;
     - terms keeping their form, and a crypto venue's fees.

# Compatibility

- Additive: one configuration map and one router field with empty defaults, one
  optional `OrderTerms` field, one result type, one optional protocol.
- A run declaring no venue, no router, and not both a plan and a queue keeps its
  `configuration_id`, `result_id`, snapshot bytes and certificate.
- A multi-venue run has new identities, by definition.
- A run that combined a plan and a queue was refused, and now runs.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- The same strategy can be measured on venue A, on venue B, and with routing
  between them, with its costs attributed per venue (S-09, GR-4).
- A TWAP's children wait their turn in the queue.
- A strategy can react to its children's fills, and nothing else changes for
  one that does not ask.
