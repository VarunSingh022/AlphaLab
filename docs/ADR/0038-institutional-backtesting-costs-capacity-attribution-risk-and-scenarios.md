# ADR-0038: Institutional Backtesting — Costs, Capacity, Attribution, Risk, and Scenarios

## Status

**Accepted and implemented in v3.3.0.**

The third capability release on the architecture frozen at v3.0.0. It deepens
two packages, adds one, and extends one shared module. No boundary moves, no
ownership changes, and every v3.1 and v3.2 invariant holds.

Depends on **ADR-0019** and **ADR-0020** for the currency rules the attribution
and scenario layers obey, **ADR-0023** for the run-state envelope this release
deliberately does not widen, **ADR-0027** for the sector provenance attribution
reads, **ADR-0028** for the settlement boundary, **ADR-0033 decision 10** and
**ADR-0037** for the rule against invented defaults, and **ADR-0014** for the
partial-fill retirement the capacity and liquidity work rests on.

---

# Context

v3.1 gave AlphaLab a dataset it could trust. v3.2 gave it research methodology.
What it still could not do is answer the questions an institution asks before
allocating to a strategy:

* **What does it actually cost to trade this?** A simulated fill carried two
  cost numbers — `slippage`, a per-unit price concession, and `commission`.
  Everything else was folded into one of them or absent. A backtest could not
  say whether a concession came from crossing a quoted spread, from an explicit
  slippage assumption, or from the order's own size, and it had nowhere at all
  to put an exchange fee or a transaction tax.
* **How much capital can it take?** `alphalab.research.capacity` degrades a
  CAGR at three fixed AUM levels from a trade count. It reads no price, no
  volume and no position, so it cannot say which name binds first or why.
* **Where did the P&L come from?** `calculate_attribution` answered strategy,
  asset and sector. Country, currency, venue, broker, factor and execution were
  not expressible.
* **Where does the risk come from?** `alphalab.risk` is a *pre-trade gate*: it
  reads an order against limits and answers "may this pass". It measures nothing
  about a book already on. `value_at_risk` and `conditional_var` existed in
  `alphalab.analytics.metrics` as single-series functions with one implicit
  method and no decomposition.
* **What happens in a crisis?** `alphalab.research.stress` subtracts a tenth
  from one observation of a return series and rescales the rest. It never sees a
  position, a price or a currency, so it cannot express "energy fell twenty
  percent" or "the euro fell against the dollar" at all.

Two of those existing surfaces share a name with what this release adds. Neither
is replaced, and section **Decision 9** records why.

---

# Decision

## 1. One execution-cost authority, itemized by role and separated by settlement

`alphalab.execution.costs` defines `ExecutionCostModel`: six roles — spread,
slippage, impact, commission, fee, tax — each one named, each one required.

It is **not** a second cost engine. It composes the two protocols that already
existed (`SlippageModel`, `CommissionModel`) and adds the four roles nothing
owned. `ExecutionSimulator` routes *every* fill through it, including a
simulator configured the pre-v3.3 way: a simulator given only `slippage_model`
and `commission_model` is exactly a cost model whose other four roles are the
named absences, and it produces byte-identical reports to the ones it produced
before. There is one costing path, not a legacy one beside a new one.

Every cost is exactly one of two settlements, and the distinction is the whole
design:

| settlement | roles | how it reaches the portfolio |
| --- | --- | --- |
| `PRICE_EMBEDDED` | spread, slippage, impact | moves the fill price |
| `CASH_CHARGED` | commission, fees, tax | debited from cash |

Collapsing the two would double-count. `PortfolioEngine.apply_fill` takes a
price and **one** cash cost, which is the single cash channel invariant 3
protects, and this release does not add a second: `ExecutionCosts.price_concession`
moves the fill price and `ExecutionCosts.cash_charged` goes down the one channel.

## 2. The ordering is stated, not left to be inferred

Several costs are computed from a price, and which price changes the answer:

```
order intent
  -> latency model            when the fill is stamped
  -> reference price          what the event showed
  -> liquidity / fill policy  how much fills            (FillPolicy, unchanged)
  -> spread                   per-unit concession
  -> slippage                 per-unit concession
  -> impact                   per-unit concession
  -> fill price = reference +/- (spread + slippage + impact)
  -> cash base  = fill price * filled quantity
  -> commission, fee, tax     charged on that cash base
  -> execution result
```

The three concessions are computed from the **reference** price and are
additive, so they do not compound. The three cash costs are computed from the
**post-concession** consideration, because that is the notional that actually
changed hands — a fee quoted as a fraction of consideration is a fraction of
what was paid, not of what was quoted.

## 3. `ExecutionReport` is not widened; the itemization is derived

`ExecutionReport` is captured into the run-state envelope by
`alphalab.runtime.snapshot` under ADR-0023. Widening it is a schema change to a
persisted record, and v3.3 does not make one.

It keeps its two cost figures, which now carry the two totals. The breakdown
behind them is **not stored**: `ExecutionSimulator.simulate_costs` is a pure
function of the instruction, the quantity and the configuration, so the
itemization for any fill recomputes exactly and for ever from the run's own
configuration. Storing it would create a second copy of a derived fact, and a
second copy is a second thing that can disagree.
`tests/regression/test_v33_invariants.py` asserts the two totals equal the six
items, on every run.

## 4. The pipeline forwards what the event showed

Before v3.3 the simulator was handed a price and a quantity and nothing about
the depth behind them, so a cost model needing a participation rate had no
denominator and refused. The fill policy has always been given this information
(`LiquidityContext`), so the gap was that one seam.

`ExecutionEngine.simulate` and `_execute_order` now forward the event's bid, ask
and shown size. Each is `None` when the event showed nothing of the kind, and a
role that needs one refuses rather than substituting a figure — so a bar feed
with no spread cannot silently produce a spread charge. Without this, the new
cost roles would have been reachable only from a library call and not from the
canonical execution path, which would have made them a decoration.

## 5. Capacity is a liquidity question, and it names what binds

`alphalab.execution.capacity.CapacityModel` connects capital, position size,
ADV, turnover, participation and market impact. Two constraints are evaluated:

* **`PARTICIPATION`** — closed form per asset:
  `C_i = participation_limit * adv * price / (weight * turnover)`.
* **`IMPACT_BUDGET`** — solved by deterministic bisection, because
  `ImpactModel` is a protocol and a square-root, linear or caller-supplied model
  each invert differently.

Portfolio capacity is the **minimum** over assets, and `binding_asset_id` names
the one that bound. A mean or a sum would describe a portfolio that cannot be
traded.

It reads the **same** `ImpactModel` protocol a fill is priced with, so a
capacity study and a backtest cannot disagree about impact. Every input is
required: there is no default participation limit, no default turnover and no
default impact budget, because each moves the answer by orders of magnitude and
none has a universal value. `impact_budget=None` reports the constraint as
*unevaluated* rather than quietly evaluating it against an invented number.

## 6. Attribution reports availability, and never fabricates

`calculate_attribution` is unchanged. `attribute()` extends the same authority to
nine dimensions and reuses `split_realized_pnl` — the one place this repository
decides how a netted fill's P&L divides between strategies.

The six new dimensions need facts `TradeRecord` does not carry, and they come
from the caller in a `TradeFacts`. They are not derived, guessed or defaulted,
because AlphaLab does not have them:

* there is **no country** anywhere in the package. `InstrumentRecord` carries a
  sector and a listing exchange, and an exchange is not a country of risk;
* **broker** never reaches an `ExecutionReport`. The report carries `venue`,
  which `test_venue_concepts_stay_distinct.py` keeps deliberately apart from
  both a listing exchange and a broker. Reading `venue` as a broker would make
  every simulated fill attribute to a broker called `"SIM"`;
* a **factor** decomposition is the output of a factor model, and which model is
  the caller's decision.

Each dimension therefore carries an `Availability`. A dimension nothing was
supplied for comes back `NO_METADATA` with an **empty** breakdown — never one
`UNKNOWN` bucket holding the whole P&L, which is the rule the sector breakdown
has followed since v2.11, applied to the rest.

Two dimensions do not reconcile, by construction rather than defect:

* **`CURRENCY`** — its buckets are denominated in *different currencies*.
  Summing them needs a rate, and ADR-0020 forbids inventing one. The breakdown
  is dimensionally coherent within each bucket and deliberately has no total.
* **`EXECUTION`** — its buckets are costs, not a partition of P&L.

**`FACTOR`** reconciles because the unexplained remainder is computed and
reported as a `residual` bucket. A breakdown that dropped it would claim the
model accounted for P&L it never touched.

## 7. Risk measurement lives in `analytics`, and the method is named

`alphalab.risk` stays the pre-trade gate and is untouched.
`alphalab.analytics.decomposition` is risk as a **measurement**, placed where
AlphaLab already measures — `value_at_risk` has been in
`alphalab.analytics.metrics` since v1.

Nothing is re-derived. Every estimator is imported from the authority that owns
it or built from `alphalab.common.statistics`, which gains `sample_covariance`:
the same `n - 1` estimator as `sample_variance`, built from the same expression
in the same order, so `sample_covariance(x, x)` is exactly `sample_variance(x)`
float-for-float. A covariance matrix whose diagonal disagreed with the variances
the repository reports would give a portfolio volatility no position's own
volatility could be reconciled against.

**VaR exposes its methodology.** "VaR at 95%" is not a number until the method is
stated. `VaRPolicy` carries method and confidence together, and `identity()`
renders the pair. Three methods are offered because three are legitimate:
`HISTORICAL` (the existing implementation, called rather than reimplemented),
`GAUSSIAN`, and `CORNISH_FISHER`. Every figure is a **non-negative magnitude of
loss**, negated once, here, and stated.

`risk_contributions` is the Euler decomposition: `CTR_i = w_i (Cw)_i / sigma_p`,
and these sum to `sigma_p` exactly. That property is what makes it a
decomposition rather than a list of per-asset volatilities. A negative
contribution is real and is kept: a position that hedges the rest genuinely
removes risk.

Degenerate samples **refuse**. A risk report that turned an undefined
measurement into `0.0` would report a riskless portfolio, which is the most
dangerous possible placeholder.

## 8. One scenario contract, generic by construction

`alphalab.scenario` is the one new package. A `Scenario` is a named, ordered list
of shocks and nothing else: no market data, no portfolio, no clock, no state.

It applies to a `ScenarioState` — a flat, immutable projection anything holding
positions can produce — rather than to a portfolio class. **That is what makes
it reusable.** A contract naming `alphalab.portfolio.PortfolioState` would be
usable by exactly one portfolio class. The package imports `alphalab.common` and
nothing else in AlphaLab, and
`test_shared_names_stay_distinct.py` asserts that it stays so.

* **Applying returns; it never mutates.** Every type is a frozen dataclass and
  every transformation is a `replace()`, so scenario leakage into the base book
  is structurally impossible rather than a rule to remember.
* **Identity is derived.** SHA-256 over the canonical rendering of the name and
  the shocks, following `alphalab.research.study`: derived from content, never
  minted, never taken from a clock.
* **Composition is ordered.** `a.then(b)` and `b.then(a)` are distinct
  identities, as they must be — each shock multiplies what the previous produced.
* **An FX shock moves the rate, not the price.** A euro holding does not fall in
  euros when the euro falls. Shocking prices instead would move both.
* **Unsupported fields are refused, not skipped.** A volatility shock on
  exposures carrying no volatility, an FX shock on a currency with no rate, and a
  scope reaching nothing all raise. A stress test that quietly dropped a leg
  would report a smaller loss under the scenario's own name, and nothing
  downstream could tell.

## 9. Historical scenarios ship as contracts, not as numbers

This is the decision the release turns on.

A **synthetic** scenario is parameterised by the caller. `flash_crash(-0.10)` is
a complete, honest statement: somebody asked what a ten percent fall would do,
and the scenario carries no claim about the world.

A **historical** scenario *is* a claim about the world. "2008" is not a
magnitude; it asserts that markets moved a particular way over a particular
window. AlphaLab ships no market data, has no dataset of its own, and cannot
acquire one.

So `CRISIS_2008`, `COVID_CRASH_2020`, `RATES_REPRICING_2022` and
`COMMODITY_SHOCK_2022` are `ScenarioDefinition` **contracts**. Each names the
episode, the window observations must be measured over, and the observations it
needs. `realize()` turns one into an applicable `Scenario` from data the caller
supplies, and refuses — naming exactly what is absent — when they do not.

A hard-coded `-0.37` for 2008 would be the same kind of invention as a default
exchange rate: a figure that looks measured, is not, and is wrong by however
much the caller's universe differed from whatever index the number was lifted
from. This is the rule `NO_RATES` already applies to FX: the empty table is the
honest state, and every consumer refuses when it needs what it has not got.

## 10. Three name collisions, kept apart on purpose

Each is recorded in `tests/regression/test_shared_names_stay_distinct.py` with
the argument a future merge would have to break first.

| name | the two things | why they cannot merge |
| --- | --- | --- |
| **capacity** | `research.estimate_capacity` degrades a CAGR from a trade count; `execution.CapacityModel` finds where liquidity binds | the research report has no liquidity to give the execution model, and the execution model has no return series to degrade. Their inputs are disjoint and their outputs share no field |
| **stress** | `research.apply_stress_tests` perturbs a return series; `alphalab.scenario` shocks a book | a merge must pick one object to operate on and loses the other's whole vocabulary. The research function has nowhere to put a scope, an FX rate or a per-asset result |
| **impact** | `MarketImpactSlippage` is a `SlippageModel` reading `(quantity, price, side)`; `ImpactModel` reads a `CostContext` carrying liquidity | the former cannot express participation, which is exactly what an impact model and a capacity model both need. They are separate roles in one `ExecutionCostModel` |

## 11. A "deterministic" model that was not

`DeterministicLatency` drew its latency from `hash(order_id)`, which for a `str`
is salted per process by PEP 456. It was deterministic *within* a run and
different on the next: the same order id drew a different latency each time the
interpreter started, so fill timestamps — and every ordering, analytic and
parity baseline downstream — did not reproduce across processes. The name said
otherwise.

It now derives the draw from `hashlib.sha256`, the stable digest
`alphalab.research.study` and `alphalab.model_registry.artifact_store` already
use. `test_v33_invariants.py` asserts it from **separate interpreters**, because
within one process a salted hash is perfectly stable and this failure is
invisible.

This is the other half of invariant 13 — no wall clock on the execution path —
stated as: no process-local entropy either.

## 12. The itemization crossing a package boundary is money, not a mix of units

`itemized(costs, quantity)` requires the quantity. The three concessions are
stored per unit and the three cash costs are amounts, so a mapping of the raw
fields would put two units in one table — and everything consuming it sums
across the roles. Summing a per-share concession into a commission is a
dimensionally incoherent number that looks entirely plausible.

The concessions are multiplied out once, and every value returned is an amount
in the fill's settlement currency, so the values sum to `ExecutionCosts.total`.

It is a function in `alphalab.execution` rather than a record in
`alphalab.analytics`: analytics must not import execution — the two are siblings
over `alphalab.core` and joining them would be a new package edge for no gain —
and a parallel six-field record in analytics would be a second definition of
this itemization. A mapping is the shape that crosses the boundary without
either.

---

# Consequences

## What this makes possible

A backtest can now state what it cost to trade, itemized and separated by how
each cost settles; how much capital the strategy could carry and which name caps
it; where the P&L came from along nine dimensions, with the ones nobody supplied
data for reported as unavailable rather than invented; where the risk comes from,
under a named VaR method, decomposed so the parts sum to the whole; and what a
stated shock would do to the book, deterministically, without disturbing it.

## What it costs

* **A new package**, `alphalab.scenario`. It imports `alphalab.common` only, so
  the package graph stays acyclic and it adds no edge to the spine.
* **One super-linear term, deliberately.** Risk decomposition builds a pairwise
  covariance matrix: `n(n+1)/2` entries over `n` assets. There is no linear
  algorithm, and sampling or assuming a diagonal would change the number rather
  than the cost. `benchmark_institutional.py` measures it separately with a
  ceiling that catches a *cubic* regression — 14.85x measured across a 4x
  workload, against 16.00x predicted for quadratic.
* **Costs are opt-in.** `ExecutionSimulator()` with no cost model is still
  frictionless, which is a legitimate and common thing to want. What is not
  legitimate is getting one without having said so, which is why `FREE` names
  its six absences explicitly.

## What is deliberately not here

* **No vendor broker adapter, and no broker field on `ExecutionReport`.** Broker
  attribution exists only as caller-supplied normalized metadata. Introducing
  vendor integration to manufacture it was out of scope and would have been the
  wrong reason to do it.
* **No historical market data.** See decision 9.
* **No country on `InstrumentRecord`.** Classification dimensions beyond sector
  remain optional future evolution (`nowandfuture.md` section 17); country
  attribution is available when a caller supplies the mapping.
* **No second cash channel**, no widening of the run-state envelope, no change
  to the accounting identity, and no change to `alphalab.risk`.

---

# Alternatives considered

**Widen `ExecutionReport` with the six cost fields.** Rejected: it is a
persisted record under ADR-0023, and the breakdown is derivable. Storing a
derived fact creates a second copy that can disagree with the first.

**Charge fees and taxes through a second cash channel.** Rejected: it breaks
invariant 3. `apply_fill` takes one cash cost, and the sum going down that one
channel loses nothing that `simulate_costs` cannot recover.

**Extend `research.capacity` and `research.stress` in place.** Rejected: both
operate on a `ResearchPayload` — a completed run's return series — and neither
can be given a price, a volume, a position or a currency without becoming a
different function with a different signature and different callers. See
decision 10.

**Ship 2008/2020/2022 with representative index moves baked in.** Rejected. See
decision 9. This was the single most consequential decision in the release.

**One universal VaR implementation.** Rejected: historical, Gaussian and
Cornish-Fisher are each legitimate and disagree most exactly where it matters.
Picking one and hiding it would have made the figure unfalsifiable.
