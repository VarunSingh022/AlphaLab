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
