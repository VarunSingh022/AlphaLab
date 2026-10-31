# ADR-0051: v4.1.1 — Information on Every Driver, Several Datasets in One Run, and the Strategy Capability Audit

## Status

**Accepted and implemented in the v4.1.1 candidate** (branch
`work/v4.1.1-future-readiness`, uncommitted), in two milestones: the audit and
information on every driver (decisions 1-6), then the continuation that closed
the engine-owned gaps the audit found (decisions 7-12), then the FUT-009
milestone, execution algorithms on the historical drivers (decision 13), then
the FUT-013 milestone, a derivative's lifecycle declared and carried out
(decision 14), then the FUT-011 milestone, a queue model for resting limit
orders (decision 15), then the FUT-002 milestone, a strategy configuration read
back as its declared type (decision 16), then -- the release number v4.2.0 set
by the maintainer -- the FUT-014 milestone, an exercise a strategy asks for from
its hooks (decision 17), then the FUT-016 milestone, richer contract terms
(decision 18), then the FUT-015 milestone, settlement-date accounting (decision
19). The release number is the
maintainer's decision: the candidate adds public names, keyword-only parameters,
an enum member and two schema steps (instrument 3 to 4, replay 1 to 2, each
reading every earlier payload), which `docs/api/PUBLIC_API.md`'s policy and
semantic versioning make a **minor** release (v4.2.0), not a patch; the ledger
records the entries under `v4.2.0`, the recommendation. It adds **no package**
and no package edge but `alphalab.api -> alphalab.macro`; no run, pipeline or
portfolio schema moves, and no v4.1 run identity moves.

---

# Context

The brief for v4.1.1 asked whether a researcher can devise a strategy AlphaLab's
authors did not anticipate -- combining assets, datasets, sentiment, macro
releases, features and models -- validate it honestly and carry it towards
production, and asked for the answer from source, tests and executed runs. The
audit (`docs/audit/STRATEGY_CAPABILITY_MATRIX.md`,
`docs/audit/STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`) found the strategy
contract open -- a registry maps an identity to any factory, and no list of
strategy kinds exists -- and point-in-time information on the execution path
since v3.12 (OFE-009); and it found four places where composition stopped:

1. Only the backtest driver delivered information. A replay of the same records
   took none, so the replay of a strategy acting on news was another run; the
   application facade took none; the live driver had no call for it.
2. A run named one dataset, and a dataset is one series kind from one source. A
   two-venue or two-provider run carried an id its author typed.
3. `context.market.bar(asset_id)` had returned `None` for every asset since
   v2.10 (KD-005), so a strategy reading bars through its context saw none.
4. The reinforcement-learning package still reported a fixed accounting defect
   as live (DOC-012).

---

# Decisions

**1. One merge rule, every historical driver.** The rule `BacktestEngine.run`
has applied since v3.12 -- an observation at its knowledge instant, after every
record of that instant and before its slice closes; one known after the data
never -- becomes three public functions in `alphalab.backtesting.engine`
(`pending_observations`, `deliver_known_before`, `deliver_remaining`), and
`BacktestEngine.run` and `ReplayBacktest.run` both call them. A caller stepping
a run by hand calls them too, and `pending_observations` given a restored run's
cursor hands a continuation exactly the deliveries it has not made.

**2. A paused replay carries what it has still to deliver -- in process.**
`PausedReplay.observations` (defaulted, so every existing construction is
unchanged) holds the outstanding deliveries, checked to be after the run's
cursor and in order. Replay schema 1 records the cursor and not the schedule,
so `replay_snapshot.capture` **refuses** a paused replay with deliveries
outstanding that its data reaches, rather than write a payload that would
resume without them; deliveries known only after the last record are never
made by any replay of it, and do not refuse. The durable form is a schema step,
accepted as FUT-003.

**3. The facade and the live driver say it too.** `alphalab.api.backtest` and
`replay` take keyword-only `observations`; `LiveSession.deliver_observation`
delivers at the session's clock (never before the record was knowable) and
routes what the strategies asked for, as `LiveSession.close_slice` does.

**4. Several datasets, one derived identity.** `alphalab.api.CombinedDataset`
keeps each part's provenance; its version is derived from the parts' versions
and content hashes **in the order given**, because that order breaks ties
between records sharing an instant. Records are merged by time, part and
position; nothing is aligned, filled or carried forward; one instrument's price
from two parts at one instant is refused. It satisfies the manifest's
`VersionedDataset` protocol, so `manifest_for_run` checks a run against it.

**5. A context's bar is the bar the market holds.** `MarketView.bar` finds the
asset's bars by their key and returns the latest, the longer interval on a tie
(KD-005).

**6. The acceptance scenario is an example, and a test holds it.**
`examples/72_compose_a_multi_asset_strategy.py` -- an equity and an index future
from two files, social-media posts and a CPI series with revisions, a
researcher-written strategy, costs, a refusing risk gate, a manifest -- is held
by `tests/integration/test_multi_asset_composition.py` to eleven properties,
including a counterfactual (late values changed; nothing before their
availability moves) and a control that leaks and is caught.

---

**7. One schedule for every timed input.** `alphalab.backtesting.schedule`
replaces decision 1's three observation functions (never released) with a
`RunSchedule` -- observations, rates, funding, cash flows, per-unit
distributions, perpetual funding rates, splits, timers and contract settlements
-- and one `ScheduleCursor` both historical drivers and a hand loop take. Each
entry is placed *before* the records of its instant (what the world is at the
instant: a rate, a funding, a cash flow, a split) or *after* them (what happens
at it: an observation, a timer, a settlement), ordered by
`(instant, place, kind, identity)`. Two entries naming one thing at one instant
are refused. What has been applied is a function of the last record a run was
handed, so a continuation needs only the schedule back, never a counter.

**8. Per-unit distributions and funding rates.** A schedule is written before
the run, so it cannot know a position's size; a dividend or coupon is declared
per unit and a funding rate as a rate, and the run books what the position held
at the instant earns or owes, at the run's own mark.

**9. A paused replay keeps its schedule durably.** Replay schema 2 records the
schedule's digest; `restore` takes the schedule back and refuses another or none.
Decision 2's refusal is withdrawn.

**10. Contracts expire.** `InstrumentRecord.last_trade_at` (outside the identity
key; instrument schema 4) declares the last trading instant, read through a
registry index so a registry with none costs the per-record path nothing. After
it the instrument's records are skipped and its requests refused before an order
exists; `ContractSettlement` closes every position at the exchange's final price
through the portfolio's one fill authority (`final_settlement=True` admits a
zero, so an option can expire worthless), cancels working orders, attributes the
settlement, and a run that ends holding an expired contract is refused.
Physical delivery and early exercise are not modelled; settling before the last
trading instant is refused.

**11. Bonds.** `AssetType.BOND` declares its economics like a future and its
maturity as its last trading instant; `alphalab.api.bond_schedule` turns a
`macro.bond.Bond` into per-unit coupons and a redemption settlement. Accrual is
carried by trading full prices.

**12. Lineage and research outputs.** `manifest_for_run(..., schedule=)` checks
the schedule against the run's deliveries and records its digest, rendered into
the identity only when given; a run over a combined dataset records each part,
checked to derive the combined identity, so a rerun is told which datasets to
supply and in what order. `alphalab.api.observations_from_indicator` lifts
the macro engine's releases. Example 73 evidences a study's features and a
fitted model on the execution path.

**13. Execution algorithms on the historical drivers (FUT-009).** An
`ExecutionPlan` routes a backtest or replay
`ExecutionRouting.ALGORITHMIC`, a new member: the pipeline accepts and holds
every parent -- risk judges it whole, allocation reserves it, the OMS keeps one
order -- refuses stop, auction and immediate terms before an order exists, and
never simulates a parent whole. The algorithms' state is **driver state**, an
`AlgorithmBook` beside the run, as the schedule's cursor is: putting it on
`RunState` or the pipeline config would move every run's record and
`result_id`, which a minor release may not. Nor is it a field of
`BacktestResult`, which stores only the run so that nothing on it can disagree
with the run (pinned by `test_run_runtime_boundary.py`): a run given a plan
returns an `AlgorithmicResult`, the same projection with the book beside it. At each event of a parent's asset,
after the run's step, the book adopts new parents, expires children past their
slice, gives working children their turn, releases what the algorithm's rule
says is due, and ends a finished parent through `apply_terminal_outcome`. A turn
is `ExecutionPipeline.work_child`: the fill policy decides what the child's
quantity gets from the event's liquidity, the simulator prices it by the run's
cost model, and the report -- against the parent, with the parent-relative
status a venue fill has -- goes through the path every fill takes. Under
`NEXT_EVENT` a child trades only at a **later instant** than it was released,
stricter than an order's "next event", because a second record at the same
instant is the same moment. The completion criterion the audit wrote, equal to
the `PaperBroker` route fill for fill, is restated: a paper venue sees no market
data and fills a market child at the parent's reference price, so equality in
price would require the backtest to ignore each child's own bar; the backtest
releases the same children, by id, quantity and instant, as `route_child_order`
sends a `PaperBroker`, and prices them at their bars. The book persists in replay
schema 2 (unreleased, so no further step), its plan supplied back and held to it
by `plan_id`, and the manifest records the plan's id when given.

**14. A derivative's lifecycle is declared by the contract and carried out by
the run (FUT-013).** `InstrumentRecord.terms` -- outside the identity key, like
`economics` -- declares `OptionTerms` or `FutureTerms`: the option type, strike,
exercise style and window, cash or physical settlement, one `Deliverable` in any
quantity per contract, and the clearing house's `AutomaticExercise` threshold or
none. Nothing is inferred from a market's name, country or currency, and there
is no list of supported markets: a market is its declared `MarketCalendar`
(zone, sessions, holidays) and `SettlementRule`, and
`alphalab.runtime.calendars.session_close` / `settles_at` turn those into a
lifecycle's instants, so a market whose conventions differ needs different
declarations, not different code (four synthetic profiles are tested, one a
Sunday-to-Thursday market with a lunch break). `Exercise`, `Assignment` and
`OptionExpiry` are inputs on the run's schedule -- after the instant's records,
exercise before assignment before expiry before settlement, so an instruction
for the expiry instant applies before the expiry resolves the rest -- and
`RunEngine` operations. Every movement goes through the portfolio's one fill
authority: a cash-settled contract closes at its intrinsic value per unit of
price, `max(S x units x m_deliverable / m - K, 0)`, against a supplied
settlement value; a physical one is surrendered at nothing while the deliverable
moves against `strike x multiplier` and is marked at the market's price; a
physical future delivers at its final price after its last variation. The
allocation ledger closes each strategy's share and books its share of the
delivery. Assignment before expiry is an input, never drawn: which written
contracts are assigned is not knowable from prices; at expiry the contract's
declared rule exercises longs and assigns shorts. An index is a new
`AssetType`, a reference value cash options settle against, never traded. What
cannot be represented or covered is refused before anything moves.

**15. A queue model decides which resting limit orders reached the front
(FUT-011).** A `QueueModel` (`alphalab.execution.queue`) given to either
historical driver, or the facade, as `queue=` keeps each resting limit order's
`QueuePosition` -- its limit and the quantity ahead of it, or unknown -- and at
each later event of its asset, before the strategy decides there, advances it
from what that event alone showed and says what of the order may fill. The run's
fill policy decides within that; the simulator prices the fill as a maker; the
OMS, portfolio, reservation and attribution settle it as every fill. The
position book is a parameter of the step, as the FX table is, and driver state,
as an execution plan's book is: a field on the pipeline state, the run state or
the run configuration would move every run's `result_id`, including runs that
declare no model, which this release does not do. So the result is a
`QueuedResult` naming the model in its execution assumptions, the manifest
records it beside the configuration, and a paused replay carries the book.
`PriceTimeQueue` is the one model shipped: price-time priority over displayed
liquidity, every rule conservative with respect to what the feed showed -- a
displayed size bounds the queue and never adds to it, an unsigned print counts
only when declared to, a touch fills only an order at the front. A venue that
allocates pro rata is another model of the protocol, not a parameter of this
one; refused beside an execution plan, whose children it would not see, and on
a run that routes to a venue.

**16. A strategy declares its configuration's type, and reads it back as that
type (FUT-002).** ADR-0050 decision 6 left a typed configuration to 5.0 because
a typed *payload* moves every `result_id`. The type need not be in the payload:
it is in the strategy's class, which a restore is handed back. A class setting
`configuration_type` (`alphalab.strategy.configuration`) is configured only with
a value of that type -- checked strictly, so that nothing reads back as another
type -- and restore reads the recorded form, the one the encoder has always
written, back as that type: a `Decimal` from its exact text, tuples, enum
members, `UUID`s, typed keys, and dataclasses through their own constructors,
so a field added since a payload was written reads back at its default and
`__post_init__` constraints hold on the way back. A type whose values cannot be
read back is refused at configure; a recorded value not of the declared type is
refused at restore, naming the strategy and the field. No schema moves, no
identity moves, and a strategy that declares nothing reads back as before.

**17. A strategy asks for an exercise the way it asks for anything: with an
intent (FUT-014).** `IntentKind.EXERCISE` names an option and a whole number of
contracts. It is not an order: the pipeline keeps it from allocation, risk and
the OMS -- allocation refuses one handed to it directly -- and carries it out at
the end of the step whose hook returned it, through the checks a scheduled
exercise passes, against the asking strategy's own contracts and attributed to
it alone. Its effect is at the asking event's instant, not after that instant's
last record, because holding it until then would be state a pause between two
records of one instant must keep, and that state would move every run's
identity; ending the step is the earliest instant it can apply and needs none.
A cash-settled contract settles against what the run has observed of its
underlying, never a value the strategy states, so no strategy reads a price it
could not have had. A refusal is not a strategy failure but a recorded
`ExerciseRefused`; a malformed instruction fails the strategy as a malformed
intent does.

**18. A contract's terms say what it delivers, from when, and the engine carries
them out (FUT-016).** Baskets, a cash component, cash in lieu of a fraction, a
consideration in another currency and Bermudan windows are declared terms of
`OptionTerms`; so are adjusted terms, each effective from its own instant,
because an exchange's adjustment for a split or a merger is a published fact with
a date -- declared on the contract it does not look ahead, and kept by the
registry the run is handed back, it moves no run's record. A package is planned
before anything moves and booked through the fill authority, its cost apportioned
by observed market values so the fills move exactly the net cash. A split of an
instrument a held contract delivers is refused unless the contract declares the
adjustment the split implies, because delivering the units of before at the
strike of before is the one wrong answer a run must never give silently.

**19. A trade's settlement instant rides on its own ledger entry (FUT-015).** The
entry expected a portfolio schema step; none is needed. An instrument declares its
settlement rule, and each of its fills' ledger transactions carries the instant it
settles, computed over its venue's declared calendar. The ledger is the persisted
record already, so what is unsettled follows from it exactly on every driver and
across every restore, and the portfolio's index of pending obligations is derived
from it rather than stored beside it. The books stay trade-date -- every figure a
run reported means what it meant -- and buying power, the one figure that gates an
order, excludes proceeds not yet received. A book whose instruments declare no
rule writes nothing new, so no run's record moves.

**20. Margin is declared by the instrument, called by the account and liquidated
through the canonical path (FUT-017).** What a position requires as collateral is
a convention of its market and of whoever extends the credit, so it is declared
on the instrument's record -- a rate of value or an amount per contract, initial
and maintenance, a haircut and a priority -- and nothing is inferred from an
asset class or a venue. The account's margin is read after every market step,
from the registry's index of margined instruments, so a run declaring none reads
nothing and records exactly what it did. A call is a risk event, and its
liquidation is each holding strategy's own target through allocation, risk and
the OMS, resting to its asset's next event: it trades at a price the market then
shows, as far as the market allows, and a step still in breach calls again from
the book as it is, never placing a second liquidation beside a working one. An
order growing a margined position is held to the initial requirement before it
is placed; a margined future spends its margin of buying power rather than a
notional no cash ever moves for. A delivery its cash cannot pay is financed only
when every deliverable declares margin terms -- the margin terms are what
extends the credit, and a deliverable declaring none is refused as before -- and
an exercise someone instructed is financed only within the initial requirement,
while an obligation is booked and called. Buying back a short is the obligation
it is, and is debited past the cash rather than stopping the run.
`alphalab.portfolio.MarginEngine` -- one rate over a book's gross exposure -- is
a different contract and is unchanged, as is the risk gate's `MarginLimit`.

**21. A strategy withdraws its own working orders by instruction, not by a new
target (EXE-012, the v4.2.0 global gap audit).** A strategy that could place a
resting order could not take it back, so re-targeting traded against its own
unfilled order. Making a target supersede the strategy's working orders would be
the natural reading, and would move the `result_id` of every run that re-targets
beside a resting order, so the capability is an instruction instead:
`IntentKind.CANCEL`, an instruction like `EXERCISE` that allocation never sizes,
carried out at the asking event after its fills and before the batch's orders,
through the cancellation every withdrawn order takes. It withdraws only what the
strategy alone contributed to; an order netted with another strategy's, a
parent an execution plan works, or an order at a venue is not the strategy's to
withdraw, and the refusal is recorded. A bracket, a one-cancels-other pair and a
trailing stop are the strategy's logic over its hooks, so no new order type is
needed.

**22. A broker's financing and a lender's borrow are declared with their dates,
and charged on what the book holds (FUT-018).** A rate is never the engine's: a
period's interest states the broker's tiers, credit rate and day count on the
schedule, and a borrow's availability and fee are a dated series on the
instrument, each in force from its own instant -- so a run reads only what was
in effect by then, and nothing changes the record of a run that declares none.
Interest is charged on the balance at the period's instant, by the period's own
dates and day count, on trade-date or, by declaration, settled cash; a fee on
each short's value at the last mark. A short beyond what a lender offers is
refused before it is placed, counting every working sale; nothing forces one
already held to be bought in. A purchase on margin is the instrument's terms,
not the account's: it spends its initial requirement and is financed, and the
margin lifecycle (decision 20) governs what follows.

---

# Consequences

**Known limitations**

- **A bond's accrued interest is carried by its price**: a run trading clean
  prices books no accrual; trade full prices.
- **A feature evaluated inside a strategy recomputes its window**: equal to the
  study's, and linear in the history per evaluation.
- **A run's record names its last delivery, not its schedule**: the schedule is
  identified by the manifest, when the producer passes it.

- **A child's fill reaches its strategy through the portfolio, not `on_fill`**:
  as a venue fill does on the live path; the strategy sees it in its next
  context.
- **Participation that completes after its window is refused**: no child
  outlives its window in a historical run, so `COMPLETE_AT_END` has nowhere to
  trade.
- **A child is checked against the venue's declaration, not by risk again**:
  risk judged the parent, whose quantity bounds every child.
- **An algorithm is assigned per asset, not per strategy**: a netted parent
  holds several strategies, and one parent is worked one way.
- **A strategy's exercise takes effect at the end of the step that asked for
  it**, at its instant, not after the instant's last record: the earliest it can,
  with no instruction held across a pause; a cash-settled one settles against
  the run's last observed underlying price, not an official settlement value,
  which a scheduled exercise carries.
- **The books are trade-date; the settlement ledger keeps cash, not units, back**:
  units bought and unsettled may be sold -- no free-riding rule -- and neither
  interest on unsettled balances nor a settlement fail is modelled.
- **A package's cost is apportioned by its components' observed values, in one
  currency**: components priced in two currencies within one package, and a
  package with no whole unit, are refused; a quanto contract fixes the rate its
  consideration converts at, not its payoff.
- **A margin requirement is each position's own, summed**: no portfolio or
  risk-based margin (SPAN, TIMS, offsets between positions), and the risk gate's
  `MarginLimit` stays a configured gross-notional cap.
- **Interest and fees accrue at each declared period's instant**: on the balance
  and the short then, not a daily average; the periods are the caller's
  cadence; borrow is the book's, with no recall or buy-in.
- **A margin call is checked after each market step and liquidated at the
  asset's next quote**: no grace period and no check between market events; a
  market order's unfilled remainder is cancelled, and a step still in breach
  calls again, sized from the book as it is.
- **A cancel withdraws only what the strategy alone has working**: an order
  netted with another strategy's, a parent an execution plan works, and an order
  at a venue are left working; amending an order in place is not modelled, and a
  new target still counts a resting order as held.
- **An insolvent run compiles no analytics**: returns on negative capital are
  undefined, so such a run keeps its books with `compile_analytics=False`.
- **The queue model reads the displayed top of the book**: depth beyond it,
  hidden and iceberg liquidity, self-trade prevention and keep-priority amends
  are not modelled; the run's own resting orders are not in the feed, so each is
  queued against the market's queue independently; no fill probability is drawn.
- **An undeclared configuration reads back as JSON reads it**: the typed form is
  the declaration's, since reading a configuration typed without one would need
  type tags in the payload, which moves every run's identity.

Each is stated in the ledger entry it belongs to (FUT-010, FUT-006, FUT-005,
FUT-009, FUT-011, FUT-002, FUT-014, FUT-015, FUT-016, FUT-017, FUT-018,
FUT-019, EXE-012, LIM-008).

**Deferred** — to v4.3 and later, pending the maintainer's decision: portfolio and risk-based margin; venue price limits and trading halts; holding exchanges.

---

# Alternatives considered

* **Recording the observation sets on `RunState`.** The honest place for them,
  and the reason it was not taken: a new field moves every run's `result_id`,
  including runs that read no observations, which the v4 policy reserves for a
  schema step and a migration row (FUT-005).
* **Recording consumption counters in the replay payload.** Unnecessary:
  what a run has applied follows from the last record it was handed, so the
  payload carries only the schedule's digest (decision 7).
* **Auto-settling an expired contract at its last mark.** A default price for
  a settlement nobody published; refused instead (decision 10).
* **Each child an OMS order of its own.** Every child would be judged by risk,
  reserve capital and carry contributions separately, so one decision would be
  booked as many and a cancelled child would release what its parent still
  committed. The live path settles children on the parent for the same reason
  (decision 13).
* **Inferring a contract's terms from its market.** "American and physical"
  is one market's equity-option convention and wrong for its index options and
  for most of the world's; terms are declared or the event is refused
  (decision 14).
* **A queue position on the pipeline state, or the model on the run
  configuration.** Each is where the fact lives, and each moves every run's
  `result_id` -- the configuration is part of the recorded run -- for a
  capability most runs do not use (decision 15). The book is threaded as the FX
  table is and persisted where the execution plan's is.
* **Type tags in a configuration's payload.** Self-describing, and readable
  without the strategy's class -- and a change to every run's record and
  `result_id`, including runs whose configuration is JSON-native, which this
  release does not make (decision 16). The class that is handed back on restore
  already knows the type.
* **A fill probability drawn per event.** A seeded draw would be reproducible,
  but a probability is a calibration of a venue's flow the engine does not have;
  the shipped model is deterministic and conservative, and a probabilistic one
  is another implementation of the same protocol.
* **Drawing assignments.** A probabilistic assignment model is a research
  assumption with a random state, not an engine fact; assignment before expiry
  is a supplied input (decision 14).
* **Filling an order the algorithm cannot work whole, with a note.** A backtest
  that substitutes an immediate fill reports an execution nobody asked for; an
  unworkable parent is rejected, with the reason, instead.
* **Aligning combined datasets to a common clock.** Forward-filling an
  asynchronous series is the look-ahead and staleness error the point-in-time
  work exists to prevent; the run's slices already say when an instant is
  complete.
* **Calling it v4.1.1.** The brief asked for that number and also forbade
  mislabelling a feature release as a patch; the additive public API decides it.
