# Execution Realism: What a Simulated Fill Assumes, and How to State It

A backtest's figures are measured under an execution model: when an order
fills, how much of it, at what price, after what delay, at which venue. Every
default AlphaLab ships is the generous choice, kept so that a run means what it
meant in the release that wrote it. v4.3 (ADR-0060, ADR-0061, ADR-0062) adds the
cautious alternative to each, opt-in, and one call that states them all.

None of this says a strategy works. It says what a run assumed, so a figure can
be read with its assumptions beside it. Start from the conservative choice in
one call, below, and relax a role only where you know what your market and
your orders do.

---

## Three rules that hold everywhere

**Every assumption is stated.** `alphalab.runtime.execution_assumptions` reads
a run's configuration and reports how it modelled execution: the fill timing,
the fill policy, the costs, the latency, the queue model, the venues. Its
`optimistic` names each generous assumption in force in a sentence; its
`settings()` are what a strategy fingerprint carries, so evidence measured under
one model is never mistaken for evidence measured under another.

**Nothing declared means nothing changed.** Every option below is opt-in. A run
that declares none keeps its record, its snapshot bytes, its `result_id` and its
`configuration_id`. A run that declares one writes the pipeline snapshot's
version 9; one that declares none writes version 8, byte for byte.

**Nothing is invented.** No option fills a role with a number nobody chose. A
cost model that charges nothing is a stated choice (`FREE`), a venue is used only
when declared, and an order no declared venue can route is refused.

---

## Start here: the conservative choice in one call

```python
from alphalab.runtime import conservative_execution

pipeline, fill_policy = conservative_execution(
    pipeline,  # your configuration; everything else kept
    costs=venue_costs,  # an ExecutionCostModel that charges something
    participation_rate=Decimal("0.10"),  # no fill above a tenth of what an event shows
    latency=ConstantLatency(0.25),  # the latency you measured, not one invented
)
config = replace(config, pipeline=pipeline, fill_policy=fill_policy)
```

It states the cautious choice in each role the sections below describe:
`NEXT_OPEN` for when an order fills, a `StrictLiquidityCappedFill` for how much,
and `POST_LATENCY_EVENT` for what latency costs, with your costs and latency as
the simulator's. All three arguments are required, and a cost model that
charges nothing is refused: a builder that invented costs would put a number
nobody chose into a run. It changes no default, and `execution_assumptions`
names no optimism for its output. A queue model, an execution plan, packages
and venues are your strategy's own choices, each declared as its section says.

---

## The defaults, and what they assume

| Role | Default | What it assumes | `optimistic` names it |
| --- | --- | --- | --- |
| When an order fills | `FillTiming.SAME_EVENT` | at the price of the event that decided it | yes |
| What it costs | the frictionless simulator | no spread, slippage, impact, fees or tax | yes |
| How much fills | `ImmediateFill` | unlimited liquidity | yes |
| A resting limit | no queue model | first in line at its price | in `passive_fills` |
| Latency | `LatencyPricing.STAMP` | latency moves the timestamp, not the price | no |
| Where it trades | one simulator | every venue costs the same | no |

The default stays `SAME_EVENT` throughout 4.x; 5.0 makes the fill timing a
required choice (D-V4).

---

## When an order fills

`ExecutionPipelineConfig.fill_timing`:

| Timing | A market order decided at a bar's close fills at | Its stamp |
| --- | --- | --- |
| `SAME_EVENT` | that close | the deciding event |
| `NEXT_EVENT` | the next bar's close | the next event |
| `NEXT_OPEN` | the next bar's **open**, a gap included | the event that delivered the bar |

Under both held timings a limit or stop order meets the next bar from its open,
as it always has; a quote, trade or tick has no open, so its turn is
`NEXT_EVENT`'s. With a trading calendar declared for the listing venue,
`NEXT_OPEN`'s intraday opening rule gives no order a turn at a pre- or
post-market bar -- though what is due still expires -- so an order decided after a
session's last bar fills at the next session's first open.

## How much fills, and what is left

`RunConfig.fill_policy` decides each fill from what the event showed:

- `ImmediateFill` -- everything asked for;
- `LiquidityCappedFill(participation_rate)` -- at most that share of the size
  the event showed, the whole order when the event shows no size;
- `StrictLiquidityCappedFill(participation_rate)` -- the same cap, refusing an
  event that shows no size rather than filling in full.

What a partial fill leaves is cancelled in the step, as before, unless
`ExecutionPipelineConfig.remainder` is a `RestRemainder`: `RestRemainder(events=2)`
keeps a market remainder trying at its asset's next two events,
`RestRemainder(seconds=300.0)` for five minutes. A turn that fills nothing counts
against the rest, and an immediate-or-cancel or fill-or-kill order is withdrawn
after its first turn whatever the policy.

## What latency costs

The simulator's `latency_model` draws each order's latency.
`ExecutionPipelineConfig.latency_pricing` says what it does:

- `STAMP`, the default -- the fill is stamped that long after its event, at the
  event's price;
- `POST_LATENCY_EVENT` -- no order meets the market before its latency has
  passed. Its first turn is its asset's first event at or after the decision
  instant plus its draw, stamped there; events before then pass it by.

At the intraday bar a latency ends within, the order was not in the market at
the bar's open or through its range, so it meets the bar's close alone, under
`NEXT_OPEN` too. A daily-or-longer bar is a session, met from its open: a
latency is taken to be shorter than the break between sessions. On its way an
order has no queue place and takes no part in a reopening auction, though an
expiry it reaches still expires it.

---

## Resting orders wait their turn

Give `BacktestEngine.run` or `ReplayBacktest.run` a queue model --
`queue=PriceTimeQueue()` -- and each resting limit order keeps its place at its
price level, from the quote, the signed prints and the bar's range, and fills
only as far as the model says has reached the front; the result is a
`QueuedResult`. An order placed where no queue is shown starts from an unknown
one.

Beside an execution plan the queue governs the plan's limit children instead:
each joins its level from the event it was released at, and the result is an
`AlgorithmicQueuedResult` holding both books. A paused replay keeps both and
resumes to the same result.

## Orders worked in children

An `ExecutionPlan` works each order in children -- TWAP, VWAP, participation,
slicing, iceberg -- each child filled by the run's own fill policy and cost
model and settled on its parent. The fill timings and latency pricing govern
the run's own simulated orders; a plan's children are worked at the events
their algorithm schedules, and stamped by their venue's latency.

A strategy learns of its children's fills through its book, and -- defining
`on_child_fill` and subscribing to `child_fills` -- at the event each happened,
its share signed (`ChildFillReceived`). What it answers is an intent like any
other, resting until its asset's next event.

## Several legs as one

A `PACKAGE` intent trades its legs together, all or none, at one instant:

```python
Intent(
    strategy_id,
    "PAIR",
    Decimal("10"),
    kind=IntentKind.PACKAGE,
    legs=(Leg(a, Decimal("1")), Leg(b, Decimal("-1"))),
)
```

`target` is the signed number of packages, so a leg trades
`target * strength * ratio`. Packages with the same legs and terms net across
strategies, never with an order for one leg; risk approves the package whole;
its legs are linked orders, worked only together, at the close of an instant
that priced every leg, every leg filled in full or none. A package that cannot
be traded -- one leg, a leg twice, a zero ratio, an unpriced leg, terms other
than a market order's -- is refused whole and recorded as a `PackageRefused`.

---

## Where an order trades

`ExecutionPipelineConfig.venues` declares each venue's model:

```python
pipeline = replace(
    pipeline,
    venues={"ALPHA": VenueExecution(alpha_simulator), "BETA": VenueExecution(beta_simulator)},
    router=CheapestVenue(),
)
```

Every simulated order is then routed, before risk, to the first of: the venue
its `OrderTerms.venue` names; the venue the router chooses; its instrument's
listing venue, when declared. One none of these routes is refused, never filled
by a model nobody chose. The routed venue's simulator prices and stamps its
fills, the execution report names the venue, and the order records where it
went, how and when. A plan's children fill on their parent's venue, and a
package's legs are routed each. `alphalab.crypto.crypto_venue_execution` builds
a crypto venue's model from its specification's maker and taker fees.

**A router is only what it prices.** `CheapestVenue` prices each order as
taking the market at each venue -- spread, slippage, impact, commission, fees
and tax at the deciding event -- and picks the cheapest. A passive order pays no
spread when it rests, so a venue cheap to take at can be the dearer venue for a
resting order: S-09 (example 82) shows a limit TWAP routed to the venue where it
then pays more. Live best execution is out of scope (BDY-026).

`examples/82_research_execution_across_venues.py` runs it end to end: one
purchase at each venue and routed between them, its commission attributed per
venue and its implementation shortfall measured; a market order priced by its
venue's latency; and a two-venue run whose costs reconcile venue by venue
(GR-4).

---

## Reading `execution_assumptions`

`execution_assumptions(pipeline, fill_policy, queue_model=None)` -- or a
result's own `execution_assumptions` -- reports the model a run's figures were
measured under. Each declared option extends the field it belongs to, so a run
declaring none reads as it always did:

| Field | What it holds | What a declared option adds |
| --- | --- | --- |
| `routing` | `SIMULATED`; `ALGORITHMIC`, each order simulated in children; or `EXTERNAL`, nothing simulated | -- |
| `fill_timing` | `SAME_EVENT`, `NEXT_EVENT` or `NEXT_OPEN` | -- |
| `fill_policy` | the fill policy, described | `; remainder=...`, a resting remainder |
| `costs` | the run's simulator's cost model, described | `; maker_commission=...`; `; venues=...` and `; router=...`, each venue's model and the router |
| `latency` | the latency model, described | `; pricing=post_latency_event` |
| `frictionless` | whether every cost role charges nothing -- every venue's, when venues are declared | -- |
| `unlimited_liquidity` | whether the policy is `ImmediateFill` | -- |
| `queue_model` | the queue model, described, or `None` | -- |
| `venue_conditions` | what the instrument registry declared -- bands, halts, auctions, margin, borrow, tick and settlement terms -- a sentence each | -- |

`optimistic` names each generous assumption in force: `SAME_EVENT`, a
frictionless model, unlimited liquidity. `passive_fills` says how a resting
limit order's fills were decided: at its limit with no queue ahead, or by the
queue model. `settings()` is what a strategy fingerprint carries: `routing`,
`fill_timing`, `fill_policy`, `costs` and `latency` always, and `queue_model`
only when declared. A declared remainder, latency pricing, venue or router
reaches a fingerprint through the description it extends.

## What else is recorded

- **The snapshot.** The pipeline snapshot writes `NEXT_OPEN`, a remainder,
  latency pricing, venues and a router only when declared, at version 9; a
  restored run is given its venues' models and its router back and is held to
  what was recorded of each.
- **The orders.** A remainder's allowance, a latency's ready instant, a
  package's links and a venue's routing record live in each order's metadata,
  which every snapshot already carries.

## Not here

- **A conservative default.** The defaults stay generous through 4.x; 5.0 makes
  the fill timing required (D-V4).
- **Live best execution.** A router chooses among declared models (BDY-026).
- **A plan's children under fill timing or latency pricing.** They trade at the
  events their algorithm schedules.
- **Latency longer than the break between sessions.** A daily bar is met from
  its open.
