# Composing a Strategy from Heterogeneous Inputs

This guide is for a researcher with an idea AlphaLab's authors did not
anticipate. It says which contracts a strategy is written against, how prices
from several sources, external information and models reach it, what the engine
guarantees about time, and where the boundaries are. Everything below is current
behaviour, checked by tests; the worked example is
[`examples/72_compose_a_multi_asset_strategy.py`](../examples/72_compose_a_multi_asset_strategy.py),
held to its properties by `tests/integration/test_multi_asset_composition.py`.
Start with [`GETTING_STARTED.md`](GETTING_STARTED.md) (example 70) if you have
never written one.

## 1. There is no list of strategy types

A strategy is any object satisfying `alphalab.strategy.StrategyProtocol` --
usually a subclass of `BaseStrategy` overriding the hooks it needs. A
`StrategyClassRegistry` maps a strategy's *identity* to a factory that builds it
from its declared parameters; it versions and discovers your code, and it
places no limit on what that code does. Nothing in AlphaLab enumerates strategy
families, and a new idea needs no change to the engine.

| Hook | Called with | When |
| --- | --- | --- |
| `on_bar`, `on_quote`, `on_trade`, `on_tick` | one market record | each subscribed record, as it is published |
| `on_slice` | `SliceClosed(timestamp, assets)` | once every record of an instant is in -- the one moment a cross-sectional decision sees every price of that instant |
| `on_observation` | `ObservationReceived(record, delivery_id, subject)` | when a point-in-time record becomes knowable |
| `on_timer` | `TimerEvent` | when the driver fires a timer |
| `on_fill`, `on_order` | the strategy's own fills and order changes | after the step that produced them |
| `on_start`, `on_shutdown`, `on_stop` | -- | lifecycle |

Subscriptions decide what is delivered: `bars`, `quotes`, `slices`,
`observations` (or `observations:<subject>`), `timers`, `*`. A hook returns
`Intent`s -- `DELTA`, `TARGET_QUANTITY` or `TARGET_WEIGHT`, with `OrderTerms`
(market, limit, stop, opening or closing auction). That is the only way a
strategy affects anything: allocation nets intents, risk gates the requests,
the OMS works them and the portfolio books the fills, exactly as for every
other strategy.

## 2. Several assets, several datasets

* **Declare every instrument.** `InstrumentRecord` gives it a derived `asset_id`;
  a future or option must declare `InstrumentEconomics` (multiplier, settlement
  model, lot), or it cannot be booked. A strategy reads the declaration from
  `context.universe.instrument(asset_id)` instead of assuming one.
* **One dataset per source and series kind.** Ingest each file with
  `alphalab.api.ingest_csv` or `ingest_rows`; it gets a version derived from its
  bytes. Read several in one run with `alphalab.api.combine_datasets` and
  `combined_market_dataset`: the run's dataset identity is derived from the
  parts, in order, and `lifecycle.manifest_for_run` checks it. Nothing is
  aligned to a common clock or forward-filled; each series keeps its own
  instants, and one instrument's price from two parts at one instant is refused.
* **Read prices from the context.** `context.market.price(asset_id)` is the
  marking price, `context.market.bar(asset_id)` the latest bar (corrected in
  v4.1.1: until then it returned `None`), and `context.history.bars(asset_id,
  limit)` the bars up to the event, never after.
* **Missing data is yours to treat.** A missing bar is a missing record; compare
  a bar's timestamp with the decision instant and decide, as example 72's hedge
  does, rather than trading on a stale price.

## 3. External information, at the instant it was knowable

Sentiment scores, news events, statement figures, macro releases, satellite or
web readings: each is an `ExternalObservation`, `InformationEvent` or
`FundamentalObservation` with a `PointInTimeStamp` that keeps four instants
apart -- observed (what the figure is about), effective, **available** (when
anyone could have known it) and ingested. Ingest rows with
`alphalab.api.ingest_observations` / `ingest_events` / `ingest_fundamentals`,
stating how availability is known:

| Rule | Meaning |
| --- | --- |
| `AvailabilityFromColumn("collected_at")` | each row states it |
| `AvailabilityAfterLag(seconds)` | a documented processing delay |
| `AvailabilityAtNextOpen(column, calendar)` | a date-only publication, placed at the next session open |
| `AvailabilityNotDeclared()` | the source does not say: every row is `UNKNOWN` and never delivered |

For a macro series the **period measured** (`ReferencePeriod`), the **release
instant** (availability) and the **vintage** (`revision`) are three columns and
stay three fields. A repeated row and an unreadable row are rejected with their
line numbers; a revision published ahead of the figure it revises is refused.

`alphalab.alt_data.delivery_schedule(set, VisibilityRule.PUBLICATION)` orders a
set's records by when each became knowable. A macro series kept in
`alphalab.macro` is lifted with `alphalab.api.observations_from_indicator`
(observed at its reference period, known at its release, the vintage numbered by
release order). Put the deliveries -- and every other timed input, section 4 --
in one `RunSchedule` and give it to the run:

```python
schedule = RunSchedule.of(observations=deliveries)
result = BacktestEngine.run(config, market, strategies, contexts, schedule=schedule)
replay = ReplayBacktest.run(config, market, strategies, contexts, schedule=schedule)
```

or through the facade, `alphalab.api.backtest(..., schedule=...)` and `replay`.
Every driver applies one rule: a record is delivered at its knowledge instant, after
every market record of that instant and before its slice closes; what a
strategy asks for in response rests until each asset's next event, so it never
fills at a price printed before the information existed; a record knowable after
the data ends is not delivered. A live session delivers with
`LiveSession.deliver_observation(..., now=...)`, never before the record could
have been known, and routes what it produced.

**What the engine guarantees, and how it is tested.** Example 72's fixture holds
a post written on 8 April and only collected on 22 April, and a CPI revision
published two weeks after the first release. The test proves from the
strategy's own log that no decision saw either early; that changing both values
changes nothing before their availability; and that a schedule built on the
*observation* instant -- the naive join -- moves decisions two weeks early and is
caught by the same check.

## 4. Money, time and contracts: the run's schedule

`alphalab.backtesting.schedule.RunSchedule.of` takes every input that happens at
an instant rather than arriving as a market record, and every historical driver
applies it through one `ScheduleCursor`:

| Input | Type | Applied |
| --- | --- | --- |
| FX rates | `FxRate` | before the records of their instant; every later step converts at the rate in force, and a mixed-currency book without one is refused (`MissingRateError`) |
| Deposits and withdrawals | `Funding` | before the records of their instant |
| Stated cash flows | `ScheduledCashFlow` | before; the amount is the total |
| Dividends, coupons, distributions | `ScheduledDistribution` | before; per unit, times whatever is held then -- a short pays |
| Perpetual funding | `ScheduledFundingRate` | before; `-(quantity x multiplier x mark x rate)`, refused without a mark |
| Splits | `ScheduledSplit` | before; working orders are cancelled, as for any split |
| Timers | `TimerEvent` | after the records of their instant, to `on_timer` |
| Contract settlement | `ScheduledSettlement` | after; see below |

Two entries naming one thing at one instant are refused. A future or option
declares `last_trade_at` on its `InstrumentRecord`: records after it are
skipped, requests after it are refused before an order exists, and a
`ContractSettlement(asset_id, price)` closes every position at the final price
(a future's last variation, an option's intrinsic value, zero for one expiring
worthless), cancelling working orders and attributing the result to the
strategies that held it. A run that ends holding an expired contract is refused.
A fixed-rate bond (`AssetType.BOND`) trades at full prices with its maturity as
`last_trade_at`; `alphalab.api.bond_schedule` turns a `macro.Bond`'s terms into
its coupons and redemption.

A contract can also declare what it **does** -- `InstrumentRecord.terms`:

```python
call = InstrumentRecord(
    "WIDGETC100",
    AssetType.OPTION,
    "XEXM",
    "GBP",
    economics=InstrumentEconomics(Decimal("100"), SettlementModel.OPTION_PREMIUM),
    last_trade_at=session_close(market, expiry_day),
    terms=OptionTerms(
        OptionType.CALL,
        Decimal("100"),
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        Deliverable(widget.asset_id, Decimal("100")),
        expires_at=session_close(market, expiry_day),
        automatic_exercise=AutomaticExercise(Decimal("0.01")),
    ),
)
```

Then `Exercise`, `Assignment` and `OptionExpiry` on the schedule
(`ScheduledExercise`, `ScheduledAssignment`, `ScheduledExpiry`) carry out its
life: inside its window -- any instant for an American contract, from the last
trade to expiry for a European one -- a cash-settled contract pays its intrinsic
value against the settlement value you supply, a physical one delivers its
deliverable against the strike and the shares are marked at the market's price,
and at expiry the declared automatic-exercise rule exercises longs and assigns
shorts. Assignment before expiry is an input you state, never drawn. A future's
`FutureTerms` may deliver too. Nothing is inferred from a market's name: its
calendar (`MarketCalendar`) and `SettlementRule` give every instant through
`alphalab.runtime.session_close` and `settles_at`, so another market is another
declaration (example 75). A delivery the book cannot pay for or would go short
on without permission is refused; baskets, cash in lieu, quanto and Bermudan
contracts cannot be stated yet (FUT-016), and cash moves at the event's instant,
not a settlement date (FUT-015).

## 5. How the orders are executed

A strategy decides what to own; how its orders trade is a separate choice, and a
backtest can measure it. Give `BacktestEngine.run`, `ReplayBacktest.run` or
`alphalab.api.backtest` an `ExecutionPlan`:

```python
plan = ExecutionPlan(AlgorithmAssignment(TWAP(6, Urgency.neutral()), 1800.0, Decimal("1")))
result = BacktestEngine.run(config, market, strategies, contexts, execution=plan)
```

Every order is then a **parent**, accepted and held by the OMS -- risk judges it
whole and allocation reserves it -- and worked in **children** at its asset's
events by the assigned algorithm (TWAP, VWAP, participation, slicing, iceberg;
`by_asset` assigns per instrument). Each child is filled by the run's own fill
policy and cost model, so a liquidity cap applies to each child and every fill
pays the spread, impact and commission the run states; the fill settles on the
parent, so attribution, the reservation and the books see one order. Under
`FillTiming.NEXT_EVENT` a child trades only at a later instant than it was
released, and never after its slice or window; a parent no algorithm can work --
a stop, an auction, a quantity off the increment, an asset with no assignment --
is refused or rejected, never filled whole. The result is an
`AlgorithmicResult`, whose `algorithms` holds every
parent, child and fill; `alphalab.execution.quality.implementation_shortfall`
measures them (example 74). A `VenueCheck` on the plan checks every child against
a venue's declared capabilities. A child's fill reaches its strategy through the
portfolio its next context shows, not `on_fill`.

## 6. Features, factors and models

A strategy computes its features in its hooks, from what it has been shown. Keep
the inputs a feature needs in the strategy's memory and declare that memory
(section 7). Research-side features (`alphalab.factor_library`,
`alphalab.api.run_study`) and validation (`alphalab.research`: walk-forward,
purged and embargoed time-series cross-validation, deflated Sharpe, multiple
testing, regimes, event studies) measure a signal before it trades. Evaluate the
study's own `FeatureDefinition`s in the strategy with `compute_feature` over the
history it has been shown, as
[`examples/73_train_and_trade_a_model.py`](../examples/73_train_and_trade_a_model.py)
does: its test holds the traded values equal to the study's at every instant
(each evaluation recomputes the window; there is no incremental evaluator). A
model is any object the strategy holds: fit it only on samples whose label was
known before the decision boundary (example 73 purges the rest), declare its
fitted parameters as the strategy's parameters so the fingerprint hashes them,
capture them as state, and seed anything stochastic. An adaptive
strategy (`alphalab.strategy.AdaptiveStrategy`) learns with a hash-chained,
replayable state. Portfolio construction (`alphalab.portfolio_optimizer.construct`,
Black-Litterman, risk parity) is a library a strategy calls and turns into
`TARGET_WEIGHT` or `TARGET_QUANTITY` intents (example 10).

## 7. State, reruns, replay and continuation

Implement `StrategyStateProtocol` (`strategy_state_version`, `capture_state`,
`restore_state`) for every piece of memory a decision reads. Then:

* a seeded run is reproduced exactly -- `lifecycle.digest_run(result).result_id`
  is the same in this process and in a fresh interpreter;
* a replay equals the backtest, and a replay paused at any boundary -- with
  information, rates or settlements still to come -- written to JSON and
  restored with fresh objects resumes into the same result; the payload records
  the schedule's digest, and restoring it with another schedule, or none, is
  refused;
* a backtest stopped at any record, written to JSON, restored with freshly built
  objects and continued with `ScheduleCursor.start(schedule, <timestamp of the
  last record taken>)` finishes byte-identical: what remains is derived from
  that record, so nothing is applied twice or lost.

For lineage, `lifecycle.manifest_for_run(..., schedule=schedule)` checks the
schedule holds the run's deliveries and records its digest in the manifest's
identity.

## 8. Boundaries

* AlphaLab ships no data, no vendor client, no broker adapter, no holiday
  calendar and no FX rates; each is the host's (`docs/INTEGRATION.md`).
* Strategy code runs in the caller's process with the caller's privileges. A
  hosted application running other people's strategies isolates them itself.
* No text-to-sentiment model, no LLM and no network access are in the engine.
* FX rates are inputs the host supplies; the engine converts with them and
  never invents one.
* A backtest simulates one venue and one cost model: choosing between venues
  (`alphalab.execution.routing.select_route`) is evaluated on the live and paper
  path, not historically.
* Depth updates reach no strategy hook (BDY-003); passive fills have no queue
  model (FUT-011).

See [`docs/audit/STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`](audit/STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md)
for what each strategy family needs and where it stands.
