# Construction in Runs: a Construction Rule Backtested as a Strategy

Until v4.3 construction was standalone. `alphalab.portfolio_optimizer` answered
what to own, and nothing on a run's path could ask it, so a construction rule
could not be backtested beside every other strategy's evidence (GAP-20).
`alphalab.rebalancing` (v4.3, ADR-0063) runs a construction rule as a strategy.
At a slice's close a `RebalancingStrategy` asks a researcher's model for a
construction problem. It constructs it, and states a certified answer as
lot-rounded targets. The run turns those into orders as it does for any
strategy.

None of this says a construction rule works. It says what each rebalance stated
and certified, and what it traded, so a backtest of the rule can be read.

---

## Three rules that hold everywhere

**Certified answers only.** A construction trades only when its result is
`OPTIMAL`, which a certificate the method checked says it is. `INFEASIBLE`,
`ITERATION_LIMIT` and `NUMERICAL_FAILURE` trade nothing. The strategy keeps
its book, and the rebalance's record says why.

**Targets, never orders.** The answer is rounded to the instruments' lots and
stated as `TARGET_QUANTITY` intents. Allocation, risk and the OMS turn them
into orders. The optimizer generates none (ADR-0063 reopens ADR-0043's
non-goal only this far).

**Nothing is assumed (AC-12).** The model states the risk model, the expected
returns and the scenarios, so the engine estimates nothing on its behalf. Each
trigger states its parameters. The capital, the execution order, the quantity
step and the order terms are stated too. A cardinality limit is the one
argument that may be left out, and leaving it out means no limit.

---

## Start here

```python
from decimal import Decimal

from alphalab.common.order_terms import MARKET
from alphalab.rebalancing import (
    AccountEquity,
    CalendarTrigger,
    Period,
    RebalanceExecution,
    RebalancingStrategy,
)

strategy = RebalancingStrategy(
    "MY-REBALANCER",
    assets=assets,  # every asset it may hold
    model=my_model,  # a ConstructionModel: problem(view) -> problem | None
    triggers=(CalendarTrigger(Period.MONTH, zone="America/New_York"),),
    capital=AccountEquity("USD"),  # what its weights are fractions of
    quantum=Decimal("1"),  # the finest quantity step
    terms=MARKET,  # the terms its targets are sent with
    execution=RebalanceExecution.SELLS_FIRST,
)
```

Subscribe it to `slices` and `fills`. Every asset it trades must be in the
run's instrument registry. A target's multiplier and lot are read from there by
`economics_for`, the rule allocation and the accounting use, so a target is
sized as it will be booked (AC-15).

---

## The model: what the strategy has observed, as a problem

A `ConstructionModel` has one method, `problem(view)`. The `RebalanceView`
carries:

| Field | What it is |
| --- | --- |
| `instant`, `assets` | The slice, and every asset the strategy may hold |
| `prices` | Each priced asset's price at the slice |
| `quantities` | The strategy's book: its share of every fill and of every order still working |
| `weights` | That book's value over the capital, for every priced asset |
| `capital` | What the weights are fractions of |
| `fired` | Which triggers fired |
| `context` | The slice's context: its history is bounded at the instant, and its universe gives membership, sectors and instruments |

The model reads what it needs and states a `ConstructionProblem`: its universe,
its risk model, its objective and its constraints. The universe must be among
the strategy's assets, each priced at the slice. A model that holds too little
returns `None`, a history shorter than its estimate needs, say. Then nothing
is constructed, traded or recorded.

A problem that charges costs, or limits turnover, must start from the
strategy's book: `LinearCosts.current` and `TurnoverLimit.current` are
`view.weights`, exactly. A turnover limit must also keep every held asset in
the universe, since selling one the problem leaves is turnover the limit
cannot see. Each is refused otherwise.

Any certified objective serves. That covers minimum variance, mean-variance,
maximum return, minimum CVaR, a researcher's quadratic objective, and the
linear program's CVaR and conditional-drawdown limits.

---

## When it rebalances: triggers

A strategy rebalances when any of its triggers fires.

| Trigger | Fires |
| --- | --- |
| `CalendarTrigger(period, zone=...)` | At the first slice of each day, ISO week or month (`Period`), by the date in a stated IANA zone |
| `CalendarTrigger(period, calendar=...)` | The same, by a calendar's trading days. An overnight session's slice belongs to the day it opened, and a slice outside every session fires nothing |
| `ThresholdTrigger(distance)` | When a weight is farther than `distance` from its last target |
| `DriftTrigger(amount)` | When the book's total distance from its last target exceeds `amount` |

A calendar trigger states exactly one of a zone and a calendar; no zone is
assumed. A calendar is taken by the `SessionCalendar` protocol, which states
`calendar_id` and `trading_day_of`. `alphalab.data.MarketCalendar` satisfies
it, and so may a researcher's own calendar.

Threshold and drift read the position the strategy has *planned*: what is
working, and any purchases it holds back, as if filled. Otherwise a rebalance
still being executed would read as a distance from its own target. Before a
first target both fire.

A researcher's trigger satisfies `Trigger`. It needs `rendering()`, its
identity lines, and `fires(moment, memory) -> (fires, memory)`. The memory is
a string the strategy keeps in its declared state, so a resumed run's
triggers are where they were.

---

## What the weights are fractions of: capital

| Rule | Capital |
| --- | --- |
| `FixedCapital(amount)` | A constant: a mandate's size, or the budget the run's allocation gives the strategy |
| `AccountEquity(currency)` | The account's cash in `currency` plus its positions in the strategy's assets, marked at the slice: capital that grows and shrinks with the strategy |
| A `CapitalRule` of your own | Whatever it states; `None`, or an amount that is not positive, skips the slice |

`AccountEquity` is the strategy's worth only when it trades the account alone,
and it converts nothing. Beside other strategies, give it its budget with
`FixedCapital`.

---

## Its book is its own

A strategy's context shows the account's portfolio, which every strategy of a
run shares. The strategy's book is its own share of each fill
(`FillEvent.attributed_quantity`) plus its share of the orders still working:
the position allocation measures its targets against. Beside a second
strategy trading one of its assets, its book stays its own and its rebalances
reconcile to it, while the account holds both
(`tests/integration/test_rebalancing_backtest.py`).

---

## How targets are sent: the execution order

A target is sized at the rebalance's prices and fills at later ones. A run's
account refuses a fill it cannot pay for (`InsufficientFundsError`), and fills
arrive in the order the assets' next events do. So the order is stated:

* **`SIMULTANEOUS`** sends every target at the rebalance's slice. A purchase
  may fill before the sale that pays for it, which a margin account can carry
  and a cash account cannot.
* **`SELLS_FIRST`** sends the sales, and holds the purchases until every sale
  has filled. They go at the first slice after the last sale, so a cash
  account pays for them from its sales. A later certified rebalance replaces
  purchases still waiting.

Either way, leave room for prices to move between sizing and filling. A net
exposure below one is the model's to state; the engine chooses no buffer.

---

## Lots, and what rounding leaves

`round_to_lots` rounds the answer's weights to each instrument's lot at the
strategy's capital, in steps of `quantum`. Each record states
`residual_weight`, the total weight rounding left out, and `below_one_lot`, the
assets whose weight rounded to nothing. Its `weights` are the targets at the
slice's prices: what the triggers measure from next.

---

## A cardinality limit

`max_names=k` constructs through `construct_with_cardinality`. It solves the
relaxed problem first, then the problem restricted to the relaxed optimum's
largest names. The record's `cardinality` says how the relaxation ended
(`WITHIN`, `RESTRICTED` or `UNRESOLVED`). An unresolved limit trades nothing.
Exact cardinality is an integer program, and stays a boundary.

---

## What is recorded

Every rebalance is a `RebalanceRecord`, the refused ones included:

| Field | What it is |
| --- | --- |
| `sequence`, `instant`, `fired` | Which rebalance, when, and what fired |
| `problem_id`, `result_id` | The construction's identities; the limited problem's under a cardinality limit |
| `status`, `cardinality`, `detail` | Whether it was certified, and why not |
| `targets`, `weights` | What it set, for every asset of the universe and every held asset it leaves (zero); empty when nothing was traded |
| `residual_weight`, `below_one_lot` | What rounding left |
| `correlation_id`, `deferred` | What its intents carry, and, under `SELLS_FIRST`, the purchases that waited |

Each intent carries the rebalance's correlation id and, in its metadata, the
rebalance's number and both identities, so a run's fills reconcile to its
rebalances. `strategy.records` holds them in order.

---

## Paused and resumed

The book, the last target, the triggers' memories, the records and any
purchases waiting are the strategy's declared state
(`REBALANCING_STATE_VERSION`). A paused run captures them, and a resumed one
continues to the same fills and records. A record's status and cardinality
outcome are written as the persistence format writes every plain enum,
`Cls.NAME`.

---

## S-01's construction, end to end

`examples/83_rebalance_a_factor_neutral_book.py` is plan scenario S-01's
construction and M9's exit gate. Eight equities in four sectors are quoted
every weekday for ten years. At the first slice of each month in New York the
model states the most expected return a book can hold under four rules:

* no exposure to a declared value factor;
* no sector above 35%;
* the CVaR of its daily loss over its last 63 returns within a limit;
* costs of five basis points on each weight traded, from its book.

It is run as an experiment of two CVaR limits, each a trial in the ledger.
Each trial rebalances 114 times. The tight limit's program of 2024-05-01
cannot be met, is proven infeasible, and trades nothing. Every other
rebalance is certified.

`tests/integration/test_rebalancing_end_to_end.py` holds it. It checks:

* the experiment's pinned record;
* the dates;
* each sampled answer, against every constraint without the solver;
* that the trials part only where the tight limit binds;
* the reconciliation of every rebalance to the book and the account.

The prices are synthetic, so nothing in the example says whether momentum
pays.

---

## What construction costs, measured

The linear program solves a CVaR program through normal equations with one
row per scenario. On the reference machine its cost grows with the cube of
the scenario count. At 12 assets with every S-01 constraint, one construction
takes:

| Scenarios | One construction |
| --- | --- |
| 52 | 0.1 s |
| 104 | 0.37 s |
| 156 | 0.95 s |
| 252 | 3.0 s |

Example 83's 228 constructions over 63 scenarios take about forty seconds,
and its two backtests a few more. WS-12's budget is a construction at 1,000
assets with CVaR over 2,520 scenarios within 60 seconds. It is not met in pure
Python, and M9's measurement of it is recorded in the register (ADR-0063
section 7). The plan meets it with the compute backend (M11, WS-15), held to
the pure-Python method by a cross-check.

---

## The standalone rules

`alphalab.portfolio_optimizer.rebalance` keeps v3's rules unchanged until 5.0:
`RebalanceTrigger`, `check_schedule_rebalance`, whose month is thirty days,
and `check_threshold_rebalance`, whose threshold defaults to 0.05. The same
holds for the `rebalance` methods of `PortfolioManager` and `PortfolioEngine`.
They are superseded since 4.3 by the triggers above, documented and not
warned.

---

## Not here

* Exact integer programs. A cardinality limit is relaxed, with its gap stated.
* Multi-period construction.
* A quadratic objective under a scenario limit, which has no certified method
  at M9.
* A currency-hedged construction across markets: S-14, at M10.
* Per-venue costs in the construction, and the walk-forward search over
  construction rules: the rest of S-01, at M13.
