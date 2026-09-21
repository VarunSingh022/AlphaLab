# Changelog

All notable changes to AlphaLab are documented in this file.

This project follows the principles of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to Semantic Versioning.

Each entry below is the record of the release it names, written at that release.
Statements inside an entry are scoped to it: where an entry says a capability is
absent or deferred, that is what was true *then*, and a later entry says what
changed. The current state of the project is in `README.md`, `ROADMAP.md` and
`nowandfuture.md`.

---

# [3.5.0] - 2026-09-21

**Strategy execution and production intelligence: the bridge between research
and real trading.**

The fifth capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust, v3.2 research methodology, v3.3 the institutional
answers and v3.4 what an instrument's numbers mean. This answers the questions
asked *after* a strategy is deployed.

One package is deepened — `alphalab.lifecycle` — and none is added. No ownership
boundary moves, no snapshot schema changes, and every v3.1 through v3.4
invariant holds.

The decisions are recorded in
[`ADR-0040`](docs/ADR/0040-strategy-execution-and-production-intelligence.md).

## What was missing

AlphaLab could research a strategy, measure it, promote it on evidence and
record that an environment should be running it. Everything after that was
outside the library.

There was no way to say **where** a strategy was: `ModelStage` calls research,
backtest and validation all `NONE`, calls paper and live both `PRODUCTION`, and
has no member for paused. A deployment recorded *that* something should run and
never **what it needs** — the data, the capital, the limits, the broker
capabilities, the budgets — which lived in somebody's head or in a release
manifest's flat mapping of strings. Health was a list of **sentences** nothing
could count by kind or compare against yesterday's. Nothing compared a
**backtest to what actually happened**. And the one reconciliation that existed
compared AlphaLab's mirror of a venue against that venue's records, never the
book against the mirror.

## Added

### The strategy progression — `alphalab.lifecycle.progression`

* `StrategyLifecycleStage` — `RESEARCH`, `BACKTEST`, `VALIDATION`, `PAPER`,
  `PRODUCTION_CANDIDATE`, `LIVE`, `PAUSED`, `ARCHIVED`. A **third axis**, not a
  replacement: `ModelStage` asks whether a registered artifact is promotable and
  `strategy.state.LifecycleState` asks whether an instance in a session is
  running.
* `LEGAL_PROGRESSION_TRANSITIONS` — every legal move stated once, read by both
  `illegal_progression_move` (a query) and `advance_progression` (the act).
  Backward moves are legal; forward skips are not; `ARCHIVED` is terminal.
* `pause_progression` / `resume_progression` / `resume_target` — only a running
  stage can be paused, and a pause returns to the stage it interrupted, derived
  from the append-only history. A paper strategy that pauses **cannot** resume
  into `LIVE`.
* `StrategyProgression` and `StageTransition` — an immutable value with a
  reason, a timestamp and an actor on every move. It names **no environment**:
  what is live *where* stays the deployment ledger's single answer.
* `PROGRESSION_MODEL_STAGES` and `progression_conflicts` — the relation to the
  registry's own stage, stated totally, **reported** when it disagrees rather
  than resolved.

### Deployment specifications — `alphalab.lifecycle.specification`

* `DeploymentSpecification` — strategy version, parameters, dataset
  assumptions, `RiskLimits`, `CapitalPolicy`, `BrokerRequirements`,
  `MarketRequirements`, `RuntimeRequirements`.
* `specification_id_for` — a SHA-256 content digest, the fourth use of the
  construction `evidence_id_for`, `compute_checksum` and
  `derive_dataset_version` share. Tagged by
  `DEPLOYMENT_SPECIFICATION_SCHEME`. An edited specification stops verifying.
* `specification_for_version` — the reference and the parameters **derived**
  from the registered `StrategyVersion`, ADR-0017's rule applied again.
* `dataset_assumption_from` — goes through `Dataset.require_provenance()`, so a
  dataset with no lineage is refused rather than given an invented identity.
* `BrokerCapabilities` / `MarketAvailability` and
  `unmet_broker_requirements` / `unmet_market_requirements` — capability
  contracts, declared by an application. No vendor is named anywhere.
* `validate_specification` — cross-field coherence a single field cannot see: an
  order cap above the position cap, an exposure cap the leverage cap can never
  fund, a quote currency the book cannot settle. It reports; it does not refuse,
  repair or default.

### Runtime health — `alphalab.lifecycle.health`

* `HealthCategory` — stale data, abnormal execution, unexpected position, risk
  breach, heartbeat loss, broker disconnect, divergence from expected state.
* `RuntimeObservation`, `ExecutionObservation`, `StateExpectation` — supplied
  readings. `None` means **not observed**, an empty tuple means observed and
  empty, and the two produce different reports. An execution's latency is
  *derived* from two supplied timestamps, so an observation cannot claim one its
  own timestamps do not support.
* `evaluate_health` — total over the seven categories: each is evaluated or
  listed in `HealthReport.unevaluated` with the reason. `observed_at` is
  supplied; nothing reads a clock.
* `HealthStatus.UNKNOWN` — a report with no findings and an unevaluated category
  is never `HEALTHY`. A regression test sweeps every subset of the seven.
* `HealthFinding` — category, severity, subject, summary and a machine-readable
  `detail` carrying `observed` and `threshold` wherever two numbers were
  compared.
* `observation_from_live_run` — the bridge from `LiveRunState`, deriving only
  what the run actually holds and leaving the rest to the caller.
  `runtime.live.live_health` is **unchanged**.

### Expected / paper / live comparison — `alphalab.lifecycle.comparison`

* `compare_runs` and `compare_expected_paper_live` over trades, fills, slippage,
  execution latency, realized P&L and exposure.
* `AlignmentKey` — `ORDER_ID` for runs sharing a seeded identifier stream
  (ADR-0022), `ASSET_AND_TIME` otherwise. Two modes, no default, and no third
  that infers one.
* `ComparisonOutcome` — `EXACT_MATCH`, `WITHIN_TOLERANCE`,
  `MATERIAL_DIFFERENCE`, `MISSING_EXPECTED`, `MISSING_OBSERVED`,
  `NOT_COMPARABLE`. A metric with no tolerance is not comparable, never
  matching.
* Money is compared **per currency** and never summed across two. A venue's
  unmeasured slippage stays `None` — "absent, not zero", which
  `execution_report_from_broker` has said since v2.3.
* `observations_from_backtest` and `observations_from_broker` — read from a
  finished run and from a normalized `BrokerState`. Exposure is **supplied**,
  because the comparison layer computes none of its own.

### Reconciliation — `alphalab.lifecycle.reconciliation`

* `reconcile_execution_state` — AlphaLab's execution state (OMS book,
  portfolio, applied fills) against a normalized `BrokerState`, joined by the
  `ExternalOrderMap` that already owns the binding.
* `MismatchCategory` — fourteen classes, each needing a different fix: missing
  and unexpected orders and fills, quantity, price and status mismatches,
  execution mismatch, position quantity, unexpected position, instrument, cash
  and lifecycle state.
* `SymbolMapping` — how a venue's symbols join AlphaLab's derived identities.
  Required, with `identity()` as a **named** choice a caller makes.
* `ReconciliationTolerances` — seven required tolerances, no default set.
* `StateReconciliation.reconciled` and `.fully_reconciled` — agreeing about what
  was compared and having compared everything are different facts. A currency
  the broker account cannot speak about is an `UnreconciledArea`, not a
  difference of zero.
* `BROKER_STATUS_EQUIVALENTS` — the relation between the OMS and broker-local
  status vocabularies, stated once.
* Neither side is authoritative and nothing is mutated.
  `broker.reconciliation.reconcile` is **unchanged** and still owns its pair.

### Tolerances — `alphalab.lifecycle.tolerance`

`Tolerance` and `ToleranceOutcome`, shared by all three comparing capabilities.
At least one bound is required — one that bounds nothing is refused at
construction — and stating both takes the more forgiving. A relative bound
permits nothing at zero, deliberately.

## Changed

Nothing. v3.5 is additive: no public surface changed shape, no default moved and
no snapshot schema was touched. `LIFECYCLE_SNAPSHOT_SCHEMA` is still `2`.

## Fixed

`resume_target` materialized the whole transition log before reversing it, so a
strategy that paused daily cost time quadratic in its own history — 50,000
pause/resume cycles took 23.2s. It walks the log backwards by index now: 0.22s,
a 104x improvement, and linear. Found by
`benchmarks/benchmark_strategy_execution.py`, held by
`tests/regression/test_v35_complexity.py`.

## Tests

`tests/regression/test_v35_invariants.py` — one authority per concept measured
from the source; cross-process determinism of a deployment identity, proven from
a fresh interpreter; the missing-data sweep over every subset of the health
categories; the no-mutation and no-durable-state sweeps; the duration-unit
sweep; and the vendor and dependency boundary.

`tests/regression/test_v35_complexity.py` — growth ratios for health evaluation,
comparison (including the disjoint worst case), reconciliation, history
construction and the resume-target read.

`tests/integration/test_v35_capabilities.py` — one ingested dataset driving a
real backtest, a real paper run and a real live run through `LiveSession`
against `PaperBroker`, then through the lifecycle, the specification, health,
the three-way comparison and reconciliation.

Three new sections in `test_shared_names_stay_distinct.py`: the three lifecycle
state machines, the two reconciliations, the two health surfaces.

Total: **5,123 → 5,399** passing, 0 skipped, 0 warnings.

## Benchmarks

`benchmark_strategy_execution.py` — lifecycle transitions, specification
identity and validation, health evaluation, the three comparison pairs and
reconciliation, each at two sizes so the scaling is visible beside the ops/sec.

## Examples

`41`–`45`: the strategy progression; deployment specifications; runtime health;
expected against paper against live; and reconciliation against a normalized
broker state.

Example 42 ingests its own rows so its dataset assumption names bytes that
exist; 44 runs a real backtest and a real paper run; 45 reconciles a real
backtest's execution state. The broker states in 44 and 45 are deterministic
fixtures in the files, and none of the five opens a connection.

## Still external

No broker connectivity, client, credential or vendor adapter; no verification
against a commercial venue; no named vendor's request shapes. A
`BrokerCapabilities`, a `MarketAvailability` and every `RuntimeObservation` are
declarations an application supplies.

## Deliberately not built

No remediation — health and reconciliation detect and report, and mutate
nothing. No supervised live *process*: restart policy, alerting and scheduling
stay an operator's concern. No per-environment promotion policy: a progression
names no environment, and the deployment ledger remains the one answer to what
is live. No durable state: a progression, a specification, a health report and a
reconciliation are values a caller holds.

---

# [3.4.0] - 2026-09-20

**Global markets and multi-asset research: conventions, contracts, and the units
that make them mean something.**

The fourth capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust, v3.2 research methodology and v3.3 the institutional
answers. This gives it the ability to say what an instrument's numbers *mean*
outside the market whose conventions had been written into the defaults.

One leaf package is added, five are deepened, and six silently-defaulted market
conventions become required. No ownership boundary moves, and every v3.1, v3.2
and v3.3 invariant holds.

The decisions are recorded in
[`ADR-0039`](docs/ADR/0039-global-markets-conventions-and-the-multi-asset-boundary.md).

## What was missing

Six defaults were US or Binance conventions presented as universals — a futures
contract's currency, an option's multiplier and exercise style, a funding
interval, a crypto contract size. `test_no_silent_financial_defaults.py` has
swept for exactly this shape since v2.17 and found none of them, because it
sweeps *function parameters* and every one of these is a *dataclass field*.

Four things were absent entirely: settlement-date arithmetic, a tick or lot
grid, a roll rule, and the implied-volatility inversion. And one thing was
present and dangerous — `Position.market_value` is `quantity * market_price`,
and every contract bridge told its caller to apply the multiplier themselves,
which a caller can do twice.

## Added

### Market conventions — `alphalab.conventions` (new)

A leaf package importing `alphalab.common` and nothing else in `alphalab`. That
is what lets `options`, `futures`, `crypto`, `portfolio`, `data` and `api` all
use it: the graph already runs `data → options → portfolio`, so an edge into any
of those would close a package cycle.

* `MarketConvention` — venue, calendar id, quote currency, settlement currency,
  multiplier, tick schedule, lot specification, settlement rule. **Every field
  required.** An instrument nobody described cannot be constructed.
* `SettlementRule` and `SettlementBasis` (`TRADE_DATE`, `TRADING_DAYS`,
  `CALENDAR_DAYS`), and `settlement_date`. The basis is required: T+2 trading
  days across a long weekend is four calendar days.
* `TickSchedule`, `TickBand`, `TickValue`, `round_to_tick`, `is_on_tick`. A
  tiered grid — the normal shape outside the US — is expressible, and a tick
  *size* (a price) and a tick *value* (money) are separate types.
* `LotSpecification`, `lots_in`, `round_down_to_lot`. A partial lot is refused
  rather than rounded: an order for 150 where the lot is 100 is either 100 or
  200, and which is the caller's decision.
* `contract_notional` — the **one site in AlphaLab** that multiplies a contract
  count by a multiplier. `ContractNotional` reports money and underlying units as
  separate fields and carries its inputs so the figure can be audited.
* `DayCount` (ACT/365F, ACT/360, 30/360 US) and `year_fraction`.
* `Compounding` (annual through monthly, plus continuous), `compound_factor`,
  `discount_factor`.

Settlement counts trading days through a one-method structural protocol,
`TradingDayCalendar`, which `MarketCalendar` already satisfies. The calendar
authority does not move and is passed in.

### Calendars and sessions — `alphalab.data.calendar`

* `add_trading_days` — the venue's own trading days, forward or backward,
  bounded and refusing rather than looping.
* `next_open`, `next_close`, `session_windows_on`. A lunch break is two windows
  and one envelope, and only the windows can say the market was shut at noon.

### Futures — `alphalab.futures`

* `ContractChain` — the listed months of one root, refusing a chain that mixes
  multipliers, tick sizes or currencies, or repeats or mis-orders an expiry.
* `RollPolicy` and `RollTrigger` — `CALENDAR_DAYS_BEFORE_EXPIRY`,
  `TRADING_DAYS_BEFORE_EXPIRY`, `VOLUME_CROSSOVER`. No default anywhere.
* `roll_schedule` returning `RollEvent`s that carry the trigger and a readable
  reason; `active_contract_at`; `continuous_segments` feeding the unchanged
  `build_continuous_series`.
* A rule **refuses the input it needs and was not given** — a trading-day
  trigger without a calendar, a crossover without observations — rather than
  approximating one from the other.
* `curve_shape` and `CurveShape`, reading every adjacent pair. A humped curve is
  `MIXED`; `curve_slope` reads the endpoints and has not changed.
* `roll_yield`, annualized, with the sign convention stated.
* `ContractMarginSpec` and `position_margin` — a clearing house's published
  figures, refused when published after the research instant.
* `contract_tick_value`, through the one tick-value site.

### Options — `alphalab.options`

* `implied_volatility` and `ImpliedVolatility`, inverting the same expression
  `black_scholes_price` rounds (now public as `black_scholes_value`).
  `ImpliedVolatilityError` in **five** cases: at or below the no-arbitrage
  floor, at or above the ceiling, unreachable at `MAX_VOLATILITY`, vega below
  `MIN_IDENTIFIABLE_VEGA`, and non-convergence.
* `ModelAssumptions` and `BLACK_SCHOLES_MERTON` — the four things the model does
  not do, as a value a figure can travel with.
* `surface_from_chain`, returning the surface **and every refusal with its
  reason**; `VolSlice`, `surface_slice`, `surface_expiries`, `term_structure`.
  Interpolation runs along strikes and never across expiries.
* `resolve_expiration` and `resolve_strategy_expiration` — `Moneyness`,
  `SettlementStyle`, `ExpirationPolicy`, `ExpirationOutcome`. Cash and
  underlying units are two separate signed quantities, and at-the-money is its
  own state.
* `signed_quantity`, `net_premium`, `net_greeks`. The leg sign convention is now
  spelled once, and a regression test keeps it that way.

### FX — `alphalab.portfolio.fx`, `alphalab.portfolio.fx_research`

* `FxRates.cross_rate(base, quote, via=...)` — triangulation as a deliberate act
  with the route named, marked `derived`, taking the older leg's `as_of`.
  `convert` still triangulates nothing.
* `FutureDatedRateError` — a rate dated after the conversion instant is now
  refused. Listed under "optional future evolution" through v3.3; v3.4's
  point-in-time rule makes it required.
* `covered_forward_rate` and `ForwardTerms` — both deposit rates, the spot date,
  the value date and the day-count basis all required. `forward_points`,
  `carry_rate`.
* `currency_exposures`, `hedge_notional` (ratio required, negative refused).
* `currency_attribution` — a reporting-currency return split into what the
  assets did and what the currency did, as an identity with no residual.

### Crypto — `alphalab.crypto`

* `VenueSpecification`, `FeeSchedule`, `LiquidityRole`, `PriceSource`,
  `trading_fee`, `cross_venue_dispersion`. Nothing defaulted.
* `funding_instants` (anchor required), `accrued_funding`, `FundingAccrual`,
  `FundingSummary`. A funding instant with no mark is refused, not carried
  forward.
* `coverage`, `observation_gaps`, `CoverageReport`, `ObservationGap` — the
  difference between a 24/7 clock and 24/7 data, reported as counts of absences
  and never as rows.

### Fixed income — `alphalab.macro.bond`

A **foundation**, and the module says so. `Bond`, `CashFlow`, `cash_flows`,
`accrued_interest`, `clean_price`, `dirty_price`, `yield_from_clean_price`,
`macaulay_duration`, `modified_duration`, `convexity`. Duration is years,
convexity is years squared, and they are never added.

`YieldCurve` gains `discount_factor_at` under a **named** compounding
convention. It does not gain a bootstrapper.

### The wire/domain contract join — `alphalab.api`

`alphalab/data/assets.py` has said since v3.1 that joining a `FutureSpec` to a
`FutureContract` "is v3.4's work". It is done, and it is in `alphalab.api`
rather than in `alphalab.data`: a joining layer belongs *above* the things it
joins, and `data` importing `futures` or `portfolio` would close a package cycle
and put a standalone engine on the ingestion path.

* `future_contract_from_spec`, `option_contract_from_spec` — a wire spec lifted
  into the domain, `float` to `Decimal` through `str` so no binary artefact
  reaches a tick size. A spec with no `contract_month` is refused rather than
  having one derived from its expiry.
* `convention_from_spec` — the calendar id, tick grid and lot grid are
  **arguments**, because no price series states them.

### Multi-asset — `alphalab.portfolio.contracts`

`ContractHolding`, `contract_exposures`, `settlement_exposures`. A position
paired with the convention that says what its numbers mean, with the multiplier
applied once and the notional denominated in the quote currency.

## Changed

Six narrow, documented breaking changes to a public API where correctness
required it — the class of change v2.17.0 last made, when it "required the
margin rates and currencies that had been silently defaulted":

| Surface | Was | Now |
| --- | --- | --- |
| `FutureContract.currency` | `"USD"` | required |
| `OptionContract.multiplier` | `100` | required |
| `OptionContract.style` | `AMERICAN` | required |
| `data.assets.OptionSpec.style` | `AMERICAN` | required |
| `FundingRate.interval_hours` | `8` | required |
| `CryptoInstrument.contract_size` | `Decimal("1")` | required |
| `compute_funding_payment(contract_size=)` | `Decimal("1")` | required |

`FxRates.convert` now raises `FutureDatedRateError` when given a rate dated after
the conversion instant. `alphalab.factor_library.primitives` now resolves a
timezone through `alphalab.data.time.resolve_zone` rather than constructing
`ZoneInfo` directly — it was the only second site, and it bypassed the domain
error `resolve_zone` raises when a host has no tz database.

## Tests

`tests/regression/test_v34_invariants.py` — one authority per concept measured
from the source, the dataclass-field sweep the parameter sweep could not see,
point-in-time guards, determinism, dimensional correctness, the boundary grep,
and the assertion that v3.4 added no durable state and no mutable type.

`tests/regression/test_v34_complexity.py` — growth ratios for roll selection,
segment construction, chain inversion and contract exposure, plus constant-cost
checks for the two numerical solvers.

`tests/integration/test_v34_capabilities.py` — five asset classes in one book,
four venues, four currencies, checked at every seam.

Three new sections in `test_shared_names_stay_distinct.py` (calendar protocols,
three margins, two exposures) and one for the two currency breakdowns.

Total: **4,764 → 5,123** passing, 0 skipped, 0 warnings.

## Benchmarks

`benchmark_conventions.py` and `benchmark_multi_asset.py`. Measured scaling is
printed beside the linear prediction rather than claimed: roll selection,
surface construction, contract exposure and crypto coverage all measure linear
at 10x steps.

## Examples

`31`–`40`: global market conventions; exchange calendars and sessions; futures
contracts and rolls; continuous futures research; options chains and Greeks;
implied volatility and the surface; FX research; crypto perpetuals and funding;
the fixed-income foundation; and a five-asset multi-currency book.

Every one declares its own calendars, rates and prices. AlphaLab ships none.

## Still external

No holiday data, no tick table, no lot schedule, no venue registry, no FX rate
spot or forward, no deposit or discount curve, no margin figures, and no
exchange adapter, API client or credential.

## Deliberately not built

A curve bootstrapper, a volatility-surface fit, an American pricing model,
interpolation across expiries, credit and optionality in the bond surface, and a
second calendar. Each is recorded in `ROADMAP.md` with the reason.

---

# [3.3.0] - 2026-09-20

**Institutional backtesting: costs, capacity, attribution, risk, scenarios.**

The third capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust and v3.2 gave it research methodology; this gives it the
answers an institution asks before allocating to a strategy. Two packages are
deepened, one is added, and one shared module is extended. No boundary moves, no
ownership changes, and every v3.1 and v3.2 invariant holds.

The decisions are recorded in
[`ADR-0038`](docs/ADR/0038-institutional-backtesting-costs-capacity-attribution-risk-and-scenarios.md).

## What was missing

A simulated fill carried two cost numbers — a per-unit concession and a
commission — and everything else an institution pays was folded into one of them
or absent. Nothing could say how much capital a strategy could take, because
nothing read liquidity. Attribution answered strategy, asset and sector and
stopped. `alphalab.risk` is a pre-trade gate and measures nothing about a book
already on. And stress testing perturbed a *return series*, so it could not
express "energy fell twenty percent" or "the euro fell against the dollar" at
all.

## Added

### Execution costs — `alphalab.execution.costs`

* `ExecutionCostModel` with **six named roles**: spread, slippage, impact,
  commission, fee, tax. Every role required; `FREE` is how a caller says "none
  of these", once and visibly.
* `CostSettlement` separates `PRICE_EMBEDDED` costs (spread, slippage, impact —
  they move the fill price) from `CASH_CHARGED` ones (commission, fees, tax).
  Collapsing the two would double-count.
* `SpreadModel` (`QuotedHalfSpread`, `FixedHalfSpread`, `NoSpread`),
  `ImpactModel` (`SquareRootImpact`, `LinearImpact`, `NoImpact`), `FeeModel`
  (`PerTradeFee`, `ProportionalFee`, `NoFee`) and `TaxModel` (`ProportionalTax`,
  `NoTax`). `SlippageModel` and `CommissionModel` are reused unchanged.
* The application **ordering is stated** in the module and asserted in tests:
  concessions from the reference price, cash costs on the post-concession
  consideration.
* `ExecutionSimulator.simulate_costs` recomputes any fill's itemization exactly
  from the run's own configuration, so the breakdown is derivable rather than
  stored.

### Capacity — `alphalab.execution.capacity`

* `CapacityModel` connecting capital, position size, ADV, turnover,
  participation and impact, reporting the capital at which a **named** constraint
  binds and the asset that bound it.
* `capacity_curve` for sensitivity: participation and impact at the worst name
  as capital grows.
* Reads the same `ImpactModel` a fill is priced with, so a capacity study and a
  backtest cannot disagree about impact.

### Attribution — `alphalab.analytics.attribution`

* `attribute()` extends the existing authority to **nine dimensions**: strategy,
  asset, sector, country, currency, venue, broker, factor, execution. It reuses
  `split_realized_pnl` rather than re-deriving the strategy split.
* `Availability` reports each dimension as `AVAILABLE`, `PARTIAL` or
  `NO_METADATA`. A dimension nothing was supplied for comes back **empty**.
* Factor attribution carries the unexplained `residual`, which is what makes it
  reconcile.

### Risk decomposition — `alphalab.analytics.decomposition`

* `VaRPolicy` carrying **method and confidence together**, with `HISTORICAL`,
  `GAUSSIAN` and `CORNISH_FISHER`. `HISTORICAL` calls the existing
  `value_at_risk` rather than reimplementing it.
* `risk_contributions`: the Euler decomposition, summing to portfolio volatility
  exactly.
* `concentration`, `leverage`, `portfolio_beta`, `correlation_matrix`,
  `covariance_matrix`, `factor_exposure`, `liquidity_risk`, `tail_ratio`, and
  `decompose` for all of it under one policy.

### Scenarios — `alphalab.scenario` (new package)

* One `Scenario` contract — a named, ordered list of shocks — applied to a
  `ScenarioState`, a flat projection any portfolio class can produce. The
  package imports `alphalab.common` and nothing else in AlphaLab.
* Four shock kinds (price, volatility, FX, liquidity) and four scopes (all,
  assets, sector, currency).
* Applying **returns**; it never mutates. Identity is a SHA-256 derived from
  content. Composition is ordered.
* Synthetic scenarios: `flash_crash`, `rate_shock`, `fx_shock`,
  `commodity_shock`, `sector_shock`.
* Historical scenarios ship as `ScenarioDefinition` **contracts** —
  `CRISIS_2008`, `COVID_CRASH_2020`, `RATES_REPRICING_2022`,
  `COMMODITY_SHOCK_2022` — each naming its window and the observations it needs,
  and refusing until a caller supplies them. **AlphaLab invents no historical
  move.**

### Elsewhere

* `alphalab.common.statistics.sample_covariance` — the same `n - 1` estimator as
  `sample_variance`, so `sample_covariance(x, x) == sample_variance(x)` exactly.
* `benchmarks/benchmark_institutional.py` measuring six surfaces with scaling
  ceilings.
* Examples `25`–`30`.

## Fixed

* **`DeterministicLatency` was not deterministic across processes.** It drew
  from `hash(order_id)`, which PEP 456 salts per interpreter, so the same order
  id produced a different latency on every run and fill timestamps did not
  reproduce. It now uses `hashlib.sha256`. Asserted from separate interpreters,
  because within one process a salted hash is perfectly stable and the failure is
  invisible.

## Changed

* `ExecutionEngine.simulate` and the execution pipeline now **forward the market
  event's bid, ask and shown size** to the cost model. Without this the new cost
  roles were reachable only from a library call and not from the canonical
  execution path. Each is `None` when the event showed nothing of the kind, and a
  role that needs one refuses rather than substituting a figure.
* `ExecutionSimulator` gained an optional `cost_model`. A simulator configured
  the pre-v3.3 way produces **byte-identical** reports to the ones it produced
  before; there is one costing path, not a legacy one beside a new one.

## Not changed

`ExecutionReport` keeps its shape — it is persisted under ADR-0023, and the
itemization is derivable. `PortfolioEngine.apply_fill` keeps its single cash
channel, so the accounting identity is untouched. `alphalab.risk` stays the
pre-trade gate. `research.capacity` and `research.stress` keep their names and
their jobs; the three name collisions this release creates are recorded in
`tests/regression/test_shared_names_stay_distinct.py` with the argument a merge
would have to break first.

---

# [3.2.0] - 2026-09-20

**Strategy research and validation: features, factors, signals, folds.**

The second capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust; this gives it the methodology that turns one into a
research result nobody has to take on trust. Two packages are deepened and one
module is added. No boundary moves, no ownership changes, and every v3.1
invariant holds.

The decisions are recorded in
[`ADR-0037`](docs/ADR/0037-strategy-research-features-validation-and-overfitting.md).

## What was missing

`alphalab.research` scored a *completed run's* returns and trades. It could say
how consistent a Sharpe ratio had been across chunks of an existing equity
curve, and it knew nothing about a dataset, a feature or a training set. Nothing
in the repository could answer the question research actually starts from:
**does this signal predict anything, and would the answer survive contact with
data it was not fitted on?**

Concretely, before v3.2 there was:

* no computational feature definition — `FeatureMetadata` is a catalogue record
  with an owner and a description, and carries no field, window or parameters;
* no feature computation at all — Feature Store deliberately computes nothing,
  and Factor Library held six single-asset style factors and no framework;
* no forward returns, no information coefficient, no decay, no turnover, no
  exposure, no ranking, no neutralization;
* no split generation of any kind, and therefore no purging and no embargo;
* `walk_forward_analysis`, which cuts a finished return series into equal
  chunks — a useful diagnostic, and not walk-forward validation;
* `parameter_robustness`, which derives an instability index from the *number*
  of parameters and perturbs nothing;
* no correlation, rank, quantile or z-score anywhere, and five private copies of
  the unbiased sample variance.

## Added

**`alphalab.common.statistics`** — the one deterministic statistics authority.
Mean, unbiased sample variance, standard deviation, median, interpolated
percentile, ranks under four stated tie conventions, Pearson and Spearman
correlation, single-regressor OLS, standardization, winsorization and quantile
bucketing under three stated tie-break rules. Every undefined statistic raises
rather than returning a placeholder: a correlation over one observation, a
variance over one, and a z-score over a constant series are not zero.

**A typed feature framework** in `alphalab.factor_library`. `FeatureDefinition`
states the kind, the source field, the window in *periods*, the parameters and
the missing-data policy, and defaults none of them — a kind that reads a window
and was not given one raises, and a parameter the kind does not read is refused
rather than ignored, because an unread parameter would still change the derived
identity. Nineteen `FeatureKind`s share one contract.

**A derived feature version.** `derive_feature_version` hashes a canonical
rendering of the definition, following `derive_dataset_version` line for line:
a scheme tag, `label=value` lines, a fixed field order, SHA-256. Two processes
describing the same feature agree on its version with no shared state.

**Feature lineage.** An `ObservationFrame` carries the dataset version it was
read from; every `FeatureSeries` computed from it inherits that, and derives a
`lineage_id` from the dataset version, the feature version, the symbol and the
zone. `require_lineage()` refuses a series computed from a dataset with no
provenance — the rule `Dataset.require_provenance` applies one layer down.

**Factor research.** Cross-sectional ranking and percentile ranking with a
stated `RankMethod`; quantile bucketing with a stated `TieBreak`; three
neutralizations that are deliberately *not* one function — `neutralize_mean`,
`neutralize_group` (exactly the residual of a regression on group dummies) and
`neutralize_beta`; `information_coefficient` reporting Pearson, Spearman, the
per-instant series and the sample counts; `factor_decay` across horizons;
`factor_turnover` under a named convention; `factor_exposure` by asset and by
any grouping the caller supplies. Every cross-sectional step is recorded on the
panel as a `FactorTransform`, so a diagnostic can say what it measured.

**Multi-asset applicability.** `feature_applicability` answers, for every
feature and every `DataAssetClass`, whether the computation means anything —
with a reason. A volume feature on an index has no such field; a *return* on an
interest rate is arithmetic that runs and a number that means nothing, because
a rate is quoted in percent and can be zero or negative.

**Signal diagnostics.** Forward-return analysis at any horizon, quantile
profiles with per-bucket counts, monotonicity as distinct from spread, and
`conditional_diagnostics` slicing by labels the caller supplies. No blended
signal score: the weights would be a judgement, and once blended a reader
cannot tell a factor with a strong monotone profile and a weak IC from its
opposite.

**Walk-forward validation.** `walk_forward_splits` produces folds with three
parts — train, validate, test — under a rolling or expanding window. Each fold
carries the *timestamps* in each part rather than the bounds they came from, so
every fold is independently inspectable and purging can be a set operation.

**Time-series cross-validation.** `cross_validation_splits` offers rolling,
expanding, purged blocked k-fold and embargoed. Purging is defined by the label
windows, read off the actual series by `label_ends_from_horizon`: twenty
observations later means twenty observations later, whatever the calendar did.
The final observations of a series, whose labels are never realized, map to
infinity and are purged from every training set.

**Robustness testing.** Parameter shifts that always change something, data and
signal perturbation, missing data by deletion, execution delay, execution cost,
block bootstrap preserving serial dependence, and independent Monte Carlo paths.
Every stochastic function takes an explicit seed and has no default;
`Perturbation` refuses a stochastic kind with no seed *and* a deterministic kind
with one.

**Overfitting diagnostics.** `parameter_sweep` evaluates and reports every
configuration, so `trials` is a true count of the search. Sensitivity, neighbour
drop, out-of-sample degradation, period and symbol stability, and a Bonferroni
threshold whose assumption is stated. `OverfittingReport` keeps measurements,
thresholds and findings in separate fields. There is no overfit score.

**A reproducible experiment contract.** `ResearchStudy` states the dataset
version, universe, features, horizons, split methodology, parameters and seed,
and derives an identity from that description. `StudyResult` derives its
identity from the study *and* the numbers, so it is tamper-evident. `run_study`
compares the dataset it is handed against the one the study names and refuses a
mismatch.

**`ValidationMethod.STUDY`** and `evidence_from_study`, deriving the dataset
from the study rather than accepting one. `evidence_id_for` is unchanged: the
rendering hashes `method.name`, so a new member changes no existing digest and
every promotion recorded since v2.6 still verifies.

**`alphalab.api`** gains `observe`, `study_panels` and `run_study` — the
`research(dataset)` that could not be written before v3.2 supplied the missing
pieces.

## Changed

**Five private copies of the unbiased sample variance** now call
`alphalab.common.statistics.sample_variance`:
`analytics.returns.annualized_volatility`,
`analytics.rolling.rolling_volatility`, `analytics.metrics.sharpe_ratio`,
`research.metrics.calculate_volatility` and
`portfolio_optimizer.metrics.calculate_volatility`. The shared function is the
same expression in the same order, so every published number is unchanged, and
each caller keeps its own guard — the shared function raises on a sample of
fewer than two, and the callers still return `0.0`.

`analytics.metrics.sortino_ratio` deliberately does **not** delegate: downside
semideviation divides by the count of all returns rather than by `n - 1`, which
is a different estimator rather than the same one on a subset.

## Not added, on purpose

Neutralization against several continuous exposures at once; a deflated Sharpe
ratio; Šidák and false-discovery-rate corrections; a t-statistic on an
information coefficient; a half-life fitted to a decay profile; any blended
score for overfitting, signal quality or research grade. Each is recorded in
`ROADMAP.md` under deliberate boundaries with the reason it is one.

## Quality

* 4,548 tests — 2,030 unit, 263 integration, 2,255 regression
* 24 examples, all runnable and all exercising real engine functionality
* 50 benchmarks
* Ruff clean, MyPy strict clean, zero skips, zero warnings
* Package-level import cycles: **0**
* Runtime dependencies: **none**, and v3.2's statistics are pure standard
  library

---

# [3.1.0] - 2026-09-20

**Universal data ingestion, provenance, and the dataset version.**

v3.0.0 froze the architecture and added no capability. This is the first release
after that freeze, and it is a **capability** release confined to one package.
`alphalab.data` gains the ingestion, validation, cleaning, provenance and
identity machinery it was named for and did not have. No boundary moves, no
ownership changes, and nothing outside `alphalab.data` is redesigned.

The decisions are recorded in
[`ADR-0036`](docs/ADR/0036-universal-data-ingestion-provenance-and-the-dataset-version.md).

## What was wrong

`alphalab.data` was called the Universal Data Engine and was 904 lines. The
module names were right and behind each was a stub:

* `parse_raw_rows` **silently dropped** every row that failed to translate, then
  **silently sorted** the survivors, out of a parser implementation of its own;
* `DataAdapter.create_metadata` fell back to `EQUITY` and `DAILY` on any
  unrecognised input, so a file of option quotes labelled `"opt"` was catalogued
  as equities;
* `evaluate_bar_quality` reported a `missing_count` that was never incremented,
  so `completeness` was 100% for every dataset that ever existed;
* `DataManager.clean` and `convert_timeframe` **replaced** `state.datasets[id]`
  in place, so the raw data ceased to exist the moment anything was done to it;
* `remove_duplicates` keyed on timestamp alone, collapsing a three-instrument
  daily file to one instrument;
* `parse_and_load` overwrote every record's symbol with the dataset's id;
* there was no CSV reader, no schema detection, no timezone handling, no
  provenance and no dataset version.

The second and fourth are the ones that mattered. A user handed AlphaLab a file
and got back a dataset with fewer rows, in a different order, with no record of
either — and ADR-0017's guarantee that evidence could not be pointed at
different data after the fact was defeated one layer down, because cleaning
replaced the dataset behind the id while the digest still verified.

## Added

**Source provenance.** `RawSource` records what was retrieved, from where, when,
and the SHA-256 of the exact bytes. `raw_source_from_path` reads a local file and
returns both the record and the bytes, so the content hashed is the content
ingested. Every other channel — HTTP, object storage, a broker export — is
recorded through `raw_source_from_bytes` by the caller that performed the
retrieval; AlphaLab fetches nothing it cannot test.

**CSV as a first-class input.** `read_delimited` handles comma, semicolon, tab
and pipe delimiters, quoted fields, headerless files and vendor header
spellings, and **preserves every discrepancy**: a row whose field count
disagrees with the header is kept as a `MalformedRow` with the reason rather
than padded or dropped. `detect_delimiter` refuses when more than one candidate
fits, and reports the share of lines that agree so raggedness stays visible.

**Schema detection.** `SchemaDetection` carries the bindings it resolved, the
roles two columns could fill, the roles nothing filled, the columns it did not
recognise, and every assumption — each with its reason, in words. `require()`
refuses unless all of it is settled and lists every problem at once. A Yahoo
export carrying both `close` and `adj close` is reported ambiguous rather than
resolved, because choosing is the difference between a backtest on raw prices
and one on adjusted prices.

**Timestamps with explicit zones.** Offset-bearing timestamps are authoritative;
a naive one is refused without a named zone rather than assumed UTC; a bare date
needs a `DateOnlyPolicy`. A numeric column whose values all read as valid
instants in both seconds and milliseconds is refused rather than guessed.

**Structured validation.** `ValidationFinding` carries a `FindingKind`, a
severity, the source line and the column. Thirteen kinds, covering duplicates,
out-of-order records, impossible OHLC, non-positive prices, negative volumes,
crossed quotes, missing values, unparseable timestamps and inconsistent
frequencies.

**Cleaning under a policy, with every change recorded.** `CleaningPolicy` has
four required fields and no defaults — ADR-0033 decision 10's rule, that a
default either way is an invented policy presented as an architectural one.
`REFUSE_EVERYTHING` is the named starting point. Every change applied returns a
`TransformationRecord` naming the operation, the count and the reason.

**There is no way to fill a missing price.** `MissingValuePolicy` has `REFUSE`
and `DROP_ROW` and no `FILL`. Forward-fill, interpolation and last-known-value
each invent a print that never happened, invisibly. The absence is structural
rather than a member that raises, so the option is not discoverable and then
refused.

**Market calendars.** `MarketCalendar` expresses a venue's timezone, weekly
sessions, lunch break, overnight session, half days and holidays. India, the
United States, Europe, Japan, Hong Kong, Singapore, Australia and a 24/7 crypto
venue are all expressible and none is privileged. **No holiday data ships** —
the position v2.11 took on taxonomies and v2.17 on FX rates.

**Multi-asset semantics.** One spec per class — equity, index, future, option,
FX, crypto, rate, commodity — each carrying what its class needs and nothing it
does not. `FxSpec` has no single `currency` because a pair's price is a ratio
between two. `OptionType` is reused from `alphalab.options.enums` rather than
spelled a third time.

**Corporate actions.** `PriceBasis` is `RAW`, `SPLIT_ADJUSTED` or
`TOTAL_RETURN`, recorded in provenance and part of the dataset's identity.
Splits adjust prices *and* volumes. Every adjustment returns an
`AdjustmentRecord` with its factor, ex-date and affected count.

**A derived, immutable dataset version.** `derive_dataset_version` hashes the
content hash, schema, zone, calendar, frequency, basis, policy and every
transformation, following `canonical_instrument_key`'s rendering exactly.
`retrieved_at` and `alphalab.__version__` are deliberately **not** in the
digest, so re-downloading an unchanged file and shipping a patch release both
leave every identity reproducible.

**The application-facing API.** `alphalab.api` — `ingest_csv`,
`ingest_rows`, `inspect_csv`, `validate_dataset`, `clean_dataset`,
`normalize_records`, `select`, `to_market_dataset`, `backtest`, `replay`. It is
deliberately not imported by `alphalab.data.__init__`, so `import alphalab.data`
pulls in no part of the execution path.

**Exact lineage into a run.** `to_market_dataset` hands the derived version to
`MarketDataset.of`, so it flows into `RunState.source_id`, out as
`BacktestResult.dataset_id`, and is hashed into `ValidationEvidence`. **The
evidence digest did not change** — `evidence_id_for` is untouched and the golden
digests pinned since v2.7 still hold. A v2.6 promotion verifies exactly as it
did; a v3.1 one additionally names the file it was measured on.

## Changed

Two v3.0 behaviours changed, both because a v3.1 requirement contradicted them
directly:

| Surface | Was | Is |
| --- | --- | --- |
| `UniversalDataEngine.clean` | `(state, id, ts)`, applying an implicit policy | `(state, id, policy, ts)`, deriving a new version |
| `DataManager.convert_timeframe` | replaced records in place | derives a new version |
| `parse_raw_rows` | dropped untranslatable rows silently | raises, naming the rows and reasons |

The first two now leave the original version untouched, record the parent in
`UniversalDataState.lineage`, and refuse to overwrite a version already held.

**`parse_raw_rows` no longer has a parser of its own.** It had its own alias
lookup, float coercion, symbol fallback and sort, and it dropped any row it
could not translate while returning the rest with nothing to say so -- a caller
who passed ten rows and received seven bars had no way to learn about the three.
It now builds a `RawTable` through `RawTable.from_rows` and coerces through
`coerce_row`, the same detection and coercion a CSV goes through, and refuses
rather than dropping. Its signature and return type are unchanged, so
`parse_and_load`, `UniversalDataEngine.load`, the examples and the benchmarks
are untouched; exactly one test pinned the old behaviour and now pins the
refusal.

There are therefore two doors onto one parser. `parse_raw_rows` returns
`tuple[Bar, ...]`, which has nowhere to put a finding, so it raises and its
message names `alphalab.api.ingest_rows` -- the door that returns a dataset
*and* a `DataQualityReport`, and can ingest the good rows while reporting the
bad ones. `ingest_rows` builds its table the same way, so the two cannot
disagree about which rows are usable.

The legacy door costs about 2.2x what it did (100,000 rows in 0.64s rather than
0.29s) because it now runs full detection and structured coercion. It stays
**linear**, and the canonical path is unaffected.

## Fixed

* `DataAdapter.create_metadata` refuses an unrecognised asset class or frequency
  instead of substituting `EQUITY` / `DAILY`.
* `remove_duplicates` keys on instrument **and** instant. Keying on the instant
  alone silently discarded every instrument but the first in a multi-symbol
  series.
* `remove_invalid_ohlc` uses the same `is_internally_consistent` predicate the
  validator does, so a record can no longer be reported invalid by one and
  dropped as valid by the other. It now also catches an `open` or `close`
  outside the high–low range, which the previous check missed.
* `parse_and_load` no longer overwrites each record's symbol with the dataset
  id, which had collapsed a multi-instrument load into one instrument.
* `DataManager.quality` records the report without replacing the dataset object.
  Quality is a measurement *of* a version, not part of it.

## Deliberately not built

* **Trade and depth ingestion from a flat file.** A `price`/`size` pair is
  indistinguishable from a partially populated bar without a declaration, and a
  depth book is not a flat table. `RecordType` has `BAR` and `QUOTE` only.
* **A Parquet reader.** The format is expressible in provenance via
  `RawSource.media_type`; reading it needs a third-party dependency, and
  AlphaLab has none. JSON needs no reader — a caller parses it with the standard
  library and hands the rows to `ingest_rows`, which takes the same pipeline a
  file does.
* **A corporate-action or holiday feed.** The boundary and the arithmetic ship;
  the data is an application's to supply, permanently.
* **Vendor broker adapters, an AI dependency, an OpenBB dependency.** None
  added. Runtime dependencies remain **zero**.

## Quality

| Metric | v3.0.0 | v3.1.0 |
| --- | --- | --- |
| Tests | 3956 | **4144** |
| Skipped | 0 | **0** |
| Warnings | 0 | **0** |
| MyPy (strict) | 910 files | **929 files** |
| Examples | 14 | **16** |
| Benchmarks | 47 | **48** |
| Runtime dependencies | 0 | **0** |

Ingestion is linear in row count, measured at 1,000 / 10,000 / 100,000 /
1,000,000 rows (10.3x, 11.7x and 10.3x for each 10x of data against a linear
prediction of 10x), and pinned by
`tests/regression/test_data_ingestion_complexity.py`.

---

# [3.0.0] - 2026-09-14

**The stable release: architecture frozen, documentation true.**

v3.0.0 adds no capability, moves no boundary, changes no schema and removes no
public name. It is the release in which AlphaLab's architecture is declared
frozen and the repository is made to describe itself accurately.

It is deliberately **additive**. Everything that would have made a v3.0 removal
breaking was taken a release early, in v2.17: seven deprecated surfaces removed
with no compatibility aliases, all four of ADR-0032's category C items
implemented, zero skipped tests and zero warnings.

## What was established

**The architecture audit.** A whole-repository audit, not limited to the
execution path, verified against the code rather than the documentation:

* **One authoritative owner per responsibility.** `ExecutionPipelineState` owns
  the execution step and `RunState` owns the run; the four drivers
  (`TradingSession`, `BacktestEngine`, `ReplayBacktest`, `LiveSession`) hold no
  state at all — none is a dataclass and none defines `__init__`. Ten snapshot
  owners, ten `capture` / `restore` / `from_primitives` triples, ten module-local
  schema constants and no aliasing.
* **Zero package-level import cycles**, measured over all 632 modules by AST
  rather than by grep. The one module-level cycle (`oms.state` ↔ `oms.snapshot`)
  is a documented deferred import inside `__serializable__`, and both import
  orders were shown safe from a cold interpreter.
* **No silent financial default.** The existing signature sweep was extended to
  dataclass field defaults and fallback expressions. Every remaining `"USD"` is a
  settlement declaration guarded by ADR-0028's two seams, or market-data
  attribution that never reaches accounting; `CapitalBudget.currency` is `""`,
  meaning *unstated*, and is refused where it cannot be determined.
* **The canonical path is linear**, re-measured at 500 → 8,000 records
  (≈2.0× per doubling), with the accounting identity holding at every size.
* **Zero runtime dependencies**, no declared entry point, no composition root.

The conclusion the audit had to reach, and did: **there is no known internal
problem that would require AlphaLab to be refactored immediately after declaring
it stable.** Everything else is classified as deliberate design, an external
dependency, or optional evolution — and `ROADMAP.md` now carries that
classification rather than a queue.

**The documentation truth freeze.** Every current-facing document was read in
full and corrected against the code. The substantive corrections:

* **`docs/ARCHITECTURE.md`** described removed packages as current architecture
  throughout its target-architecture half — `production`, `integrations`,
  `kernel` and a top-level `events` package appeared in the layer diagrams, the
  dependency rules, the package categories, the state-ownership table and the
  event-ownership table. Its state round-trip table still marked
  `ExecutionPipelineState` and `RunState` as **not** round-tripping, which has
  been false since v2.9 and v2.14; and it twice said a strategy does not see the
  marked portfolio, which has been false since v2.10 and which the same document
  contradicted two sections earlier. Its version history stopped at v2.0.0.
* **`README.md`**'s version badge read `2.13.0` while its own status table read
  `2.17.0`; its test counts disagreed with each other (3949 in one section, 2926
  in another) and with the suite (3956); its example table listed 12 of 14; its
  repository tree listed three removed packages; and its footer read `v2.11.0`.
* **`ROADMAP.md`** carried present-tense claims from v2.3, v2.4 and v2.5 that had
  since become false — "there is no connectivity to any real venue in this
  repository", "there is no object store in this repository", "no broker adapter
  reaches any venue" — said the lifecycle path was "deliberately not joined" to
  the execution path four releases after v2.16 joined it, described `kernel` as
  warning at import after v2.17 removed it, and had no v2.12 entry at all.
* **`docs/README.md`**, **`docs/SYSTEM_DESIGN.md`**, **`docs/VISION.md`**,
  **`docs/STATE_MODEL.md`**, **`docs/EVENT_MODEL.md`**,
  **`docs/GETTING_STARTED.md`** and **`docs/ENGINEERING_GUIDELINES.md`** each
  named removed packages or removed state types as current. `docs/VISION.md`
  additionally listed three items as remaining future work — wiring `replay` into
  the execution path, mark-to-market repricing, and consolidating the data
  surfaces — that were delivered in v2.2, v2.1 and v2.3 respectively.
* **A composition claim repeated since v2.4 was imprecise.** `README.md`,
  `docs/README.md`, `docs/ARCHITECTURE.md` and ADR-0013 said
  `alphalab.lifecycle` composes `research_assistant`. It does not
  import it: it imports `research`, `studio`, `enterprise`, `experiment_tracking`,
  `model_registry`, `deployment_manager` and `backtesting`, and takes the
  `StrategyDefinition` that `research_assistant.to_strategy_definition` produces.
  The dependency runs through the definition, not the package.
* **`SECURITY.md`** said AlphaLab depends on third-party Python packages. It
  declares `dependencies = []`.
* **The six strategy-runtime design documents** under
  `docs/architecture/strategy/` were pre-implementation design briefs with no
  status marker except one. Each now carries one, recording what shipped and
  where the implementation deliberately diverged.
* **ADR-0015** was still labelled `Proposed (v2.6)` although v2.6.0 shipped it
  and four documents cite it as normative. It is the only ADR whose status was
  wrong.

### Changed

- `pyproject.toml`, `alphalab.common.version` and `tests/unit/test_package_metadata.py`
  declare **3.0.0**. The `Development Status` classifier moves from
  `4 - Beta` to `5 - Production/Stable`.
- The v2.17.0 entry below reported `3949 passed`. The tagged tree reports
  **3956**; the figure is corrected there rather than carried forward.

### Added

- **`nowandfuture.md`** — the long-form project reference: purpose, ownership
  package by package, canonical models, schemas, invariants, what must not be
  changed casually, what is deliberately not implemented, what is external, and
  what optional evolution remains.

### Not changed

No source behaviour. No public name added, removed or renamed. No schema
constant moved. No test weakened: the suite is the same 3956 tests, and the only
test edited is the metadata test that pins the declared version.

---

# [2.17.0] - 2026-09-14

**The final engineering release.**

v2.17 exists so that v3.0 has nothing left to do but freeze. It does two things:
implements everything ADR-0032 deferred, and builds the three capabilities
ADR-0033 explicitly left open — "settlement-level multi-currency trading, with
the four blockers in decision 13; a strategy-class registry for the lifecycle
join; and a rate feed."

**The property that makes the three additions and not a rewrite**, pinned by
`test_no_capability_moved_another_ones_boundary`: the registry added no field to
`RunState` (still eight) or `ExecutionPipelineState` (still sixteen); FX rates
are still not run configuration *and* still not run state; multi-currency moved
one pipeline schema and one portfolio schema and no other constant; and the feed
took no dependency on the execution path.

## 1. Settlement-level multi-currency

All four of ADR-0033 decision 13's blockers are gone.

**Settlement truth and reporting truth are different numbers and stay apart.**
`PortfolioState.realized_pnl` and `commission_paid` were single cumulative
scalars naming no currency and are now `CurrencyAmounts` — currency to exact
amount — so a EUR fill accrues EUR P&L permanently and nothing is summed across
two. A valuation names one currency and converts into it on demand, recording
every rate. Translating at fill time would have been the smaller change and is
wrong: it destroys the only record of what was actually earned and bakes one
instant's rate into a cumulative figure.

**`ExecutionPipelineConfig.also_settles`** says which currencies a pipeline may
settle, and is **empty by default** — the single-currency pipeline every run had
before v2.17, byte-identical to it. Both ADR-0028 seams change from "equals the
settlement currency" to "is one of them", an `OrderInstruction` is stamped with
the *instrument's* own currency, and the resulting book is genuinely mixed — so
every valuation of it needs a rate table and refuses without one.

**Settlement conversion is an act, never a consequence.** A run that settles a
currency it has not funded is refused by `InsufficientFundsError`, and that
refusal is the capability: no fill converts cash to cover itself.
`PortfolioEngine.convert_cash` and `ExecutionPipeline.fund` / `.convert_cash` are
how the money gets there, recording both currencies, both amounts, the rate, its
`as_of` and its source on a new `CashConverted` event.

**Blockers 3 and 4.** `CapitalBudget.currency` is `""` — *unstated*, not
`"USD"` — which a single-currency pipeline determines and a multi-currency one
refuses. `_sync_risk_from_portfolio` reads `cash_in` rather than
`cash.balance(base)`, which had silently dropped every non-base balance; a run
holding most of its capital abroad would have reported almost no buying power and
refused every order, with nothing saying why.

**`alphalab.allocation` still knows nothing about exchange rates**, and that is
measured: threading an `FxRates` into it pulled all eighteen
`alphalab.portfolio` modules into a package that previously imported none of
them. The conversion happens at the pipeline and allocation receives a second
price map.

`PORTFOLIO_SNAPSHOT_SCHEMA` moves 2 → 3 and **refuses version 2**: a v2 payload
records `realized_pnl` as a bare number in no currency, and choosing one for it
would be a guess about money. `PIPELINE_SNAPSHOT_SCHEMA` moves 2 → 3 and keeps
v1 and v2 readable, because a v2 payload's missing `also_settles` genuinely
*means* the empty set. A default is allowed only when it is what the payload
already meant.

## 2. The FX rate feed

**AlphaLab still ships no FX data.** `alphalab.portfolio.fx_feed` is the
*boundary*, and it adds a contract rather than a single rate.

Without one, every caller folded quotes into a table itself and decided, alone
and usually implicitly, what to do about three things. A later quote is
`APPLIED`; a byte-identical one is a `DUPLICATE` (a venue redelivers after a
reconnect); an **older one is `SUPERSEDED` and not applied** — accepting it would
move the book's view of the market backwards because two packets arrived out of
order. Two quotes claiming one instant that disagree are **refused**, not ranked.

`FxRateSource` takes `MarketDataSource`'s shape: identity, provenance, and
nothing about order. `SequenceFxSource` is the deterministic source.
`FxFeedState` is durable, so a replayed run values its book with the rates the
original used. Staleness stays at conversion time; `silent_for` answers the
different question of whether the connection has gone quiet.

## 3. The strategy-class registry

The join ADR-0033 left as "the caller's knowledge". It is **not** in
`alphalab.lifecycle`, which still constructs nothing and still names no runtime
type — it is in `alphalab.strategy`, beside the protocol being registered.

It is not a second `StrategyDefinition`: it stores an identity, a factory and a
qualified name for provenance. It **never resolves a name to code** — no
`importlib`, no class-name derivation, because a name is not a type. A duplicate
registration is refused naming both incumbent and challenger, an unknown identity
is refused listing what *is* registered, and a factory returning something
undispatchable is refused at construction rather than at its first market event
where `Dispatcher` would blame the *strategy*.

`RunPlan.definition` was typed `object` and is now typed as itself, so it can be
handed straight to the registry.

## 4. Every ADR-0032 category C item

**Quadratic accumulation** is gone from eight standalone packages; two more
(`integrations`, `production`) were removed instead. Measured over a
2,500 → 20,000 doubling sweep: `distributed` 38.4s → 0.18s and 4.1x → 2.1x per
doubling, `feature_store` 7.8s → 0.18s, `plugins` 12.6s → 0.26s, and every
converted package now grows at ~2.0–2.1x.

**Profiling first is what made it work.** In three of them the containers were
not the dominant term: `distributed` spent ~65% re-sorting its queue per
submission and ~26% building a union of four containers per validation; `plugins`
spent ~54% calling `metadata()` on every registered plugin; `feature_store`
scanned the whole registry per registration. Converting the containers alone
would have left ~90% of `distributed`'s cost in place and made `feature_store`
**four times slower**. Each is now a derived index carried on the state, the v2.2
OMS pattern.

One term is deliberately left super-linear. `OptimizerState.pending_trials` needs
a start offset on `AppendOnlyLog`; that was implemented and measured at **+3.9%**
on `benchmark_execution_pipeline` across four interleaved runs, and refused — the
same trade ADR-0028 decision 7 refused at +1.78%.

**`AppendOnlyLog.__iter__`** became `itertools.islice`: 0.068s → 0.008s over
10,000 elements, an 8x constant every engine was paying. Bounded by index, so an
append mid-iteration is still never yielded.

**`ResearchPayload.parameters`** is genuinely immutable — copied into a
`MappingProxyType`, closing both the write-through-the-payload route and the
write-through-your-own-dict route. The annotation alone would have closed
neither. It also found a codec defect: a `MappingProxyType` is not a `dict`, so
`dataclass_to_dict` did not recurse into it.

**`BrokerProtocol`** no longer names two contracts.
`alphalab.brokers.protocol.BrokerProtocol` is `BrokerConnectorProtocol` — the
word this package already uses for its state, engine and error. **No alias.**

**`Dispatcher.dispatch_event`** takes `StrategyInboundEvent` instead of `Any`,
narrowed by naming the supertype both event families share. That needs no market
import and moves no hook selection, so no second dispatch authority appears.

## 5. Seven deprecated surfaces removed

`kernel`, `integrations`, `production`, `core.events`, `CommonEvent`, the
nine-module `alphalab.persistence` store and the ten-module orphan
`alphalab.runtime` lifecycle. All were scheduled for v3.0; removing them there
would have made the stable release a breaking one. All had **zero production
importers**.

**No compatibility aliases.** No removed name is re-exported, redirected or
served by a `__getattr__` — and `test_removed_surfaces_stay_removed.py` also
checks that none is re-exported under a *different* spelling, which would
preserve the ambiguous architecture while passing every other assertion. The
persistence **codec spine** and `RunStateStore` are untouched; that distinction
is why the store's notice was PEP 562 in the first place.

## 6. Zero skips, zero warnings

v2.16 reported `3767 passed, 2 skipped, 94 warnings`. v2.17 reports **3956
passed, 0 skipped, 0 warnings**, and neither was achieved by configuration.
*(This paragraph read "3949 passed" until the v3.0 documentation audit measured
the tagged tree and found 3956.)*

The two skips were a **gap**, not a duplicate: `SURFACES` carried `None` for the
`history` and `universe` views, so two of the six `StrategyContext` surfaces had
no structural check at all. The 94 warnings are gone because the code that
emitted them is gone. `pytest -q -W error::DeprecationWarning` passes, and a test
imports every module in the tree in a fresh interpreter with the warning fatal.

## 7. What the audit found that nothing had recorded

- **An invented Reg-T margin rate.** `MarginEngine.initial_margin` defaulted to
  `0.50` and `maintenance_margin` to `0.25`. A convention is not a universal, and
  the default produced a requirement computed against a policy nobody chose. Both
  are now required, as are `BrokerEngine.initialize(currency)` and
  `open_option_position(currency)`.
- **`from alphalab.common import *` raised `AttributeError`**, because `__all__`
  advertised `"Registry"`, which nothing defined.
- **`PersistentMap` and `PersistentSet` were reachable from no package surface**,
  while their sibling `AppendOnlyLog` was — despite typing ~100 public dataclass
  fields. AlphaLab ships `py.typed`, so a declared type a caller cannot import is
  a contract they cannot write against.

### Added

- `alphalab.portfolio.amounts.CurrencyAmounts`, `alphalab.portfolio.fx_feed`
  (`FxFeed`, `FxQuote`, `FxRateSource`, `SequenceFxSource`, `FxFeedState`,
  `FxFeedDecision`, `FxFeedOutcome`, `ConflictingQuoteError`, capture/restore),
  `alphalab.strategy.registry` (`StrategyClassRegistry`, `StrategyDeclaration`,
  `StrategyFactory`, `StrategyRegistration`, `DuplicateStrategyError`,
  `UnknownStrategyError`, `instances_for`, `runtime_for`).
- `ExecutionPipelineConfig.also_settles`, `.settlement_currencies`,
  `.is_multi_currency`; `ExecutionPipeline.fund`, `.convert_cash`;
  `PortfolioEngine.convert_cash`; `PortfolioState.settlement_currencies`;
  `CapitalBudget.currency`, `.states_currency`, `.in_currency`;
  `portfolio.events.CashConverted`; `RunPlan.strategy_id`.
- `PortfolioSnapshotProtocol.realized_pnl_in` / `.commission_paid_in`.
- `DistributedState.queued_ids`, `FeatureStoreState.registered_feature_ids`,
  `PluginState.registered_names` — derived indexes.
- `AppendOnlyLog.rest` is **not** added; see section 4.
- `alphalab.common` now exports `PersistentMap` and `PersistentSet`;
  `alphalab.portfolio` now exports `CurrencyAmounts`.
- A `rates` parameter on `process_record`, `process_quote`,
  `process_market_event`, `apply_execution_report`, `RunEngine.advance` and
  `apply_broker_execution`, each defaulting to the empty table.
- `examples/14_multi_currency_settlement.py`.

### Changed

- `PortfolioState.realized_pnl` / `commission_paid` are `CurrencyAmounts`.
- `PortfolioEngine.apply_fill` requires `currency`.
- `PortfolioSnapshotProtocol.realized_pnl` / `.commission_paid` are mappings.
- `alphalab.brokers.BrokerProtocol` → `BrokerConnectorProtocol`.
- `ResearchPayload.parameters` is a read-only `Mapping`.
- `Dispatcher.dispatch_event` / `StrategyEngine.process_event` take `BaseEvent`.
- `StrategyProtocol` is `runtime_checkable`.
- `MarginEngine` rate parameters, `BrokerEngine.initialize(currency)` and
  `open_option_position(currency)` are required.
- Eight standalone states hold `AppendOnlyLog` / `PersistentMap` / `PersistentSet`.
- `PORTFOLIO_SNAPSHOT_SCHEMA` 2 → 3 (refuses v2); `PIPELINE_SNAPSHOT_SCHEMA`
  2 → 3 (reads v1, v2, v3).
- `examples/05_broker_connection.py` rewritten against the canonical broker
  boundary; `benchmarks/benchmark_persistence.py` rewritten against
  `RunStateStore`.

### Removed

- `alphalab.kernel`, `alphalab.integrations`, `alphalab.production`,
  `alphalab.core.events`, `alphalab.common.CommonEvent`, the nine-module
  `alphalab.persistence` store, the ten-module orphan `alphalab.runtime`
  lifecycle. No aliases.
- `alphalab.common.__all__`'s non-existent `"Registry"`.
- `benchmarks/benchmark_event_pipeline.py`, `benchmark_integrations.py`,
  `benchmark_production.py` — they benchmarked removed packages.

---

# [2.16.0] - 2026-09-14

**The integrated runtime, governance, and FX — plus the refactor audit.**

Three production capabilities, each closing a *join* rather than filling a hole,
and a deliberate audit that classified every remaining structural finding. Two of
the three were designed years before they were built: ADR-0030 listed
`LiveDriver` in its Tier-3 table as "(later)", and ADR-0018 was written in v2.7
and deferred "so that the next release starts from a decision rather than from a
rediscovery". This release implements those decisions rather than redesigning
them.

**The property that makes them additions and not a rewrite**, pinned by
`test_no_capability_moved_another_ones_boundary`: the live driver added no field
to `RunState` and moved no run schema; FX added no field to run configuration and
moved no pipeline schema; governance moved exactly one schema, and only its own.

## 1. Integrated runtime

`runtime/session.py` said it against itself: "a live session driven by this
module still produces working orders and stops". Every half of the live path
existed and was tested; what nothing owned was the **cycle**.

**`alphalab.runtime.live.LiveSession`** is that cycle — settle, advance, route —
a Tier-3 driver on ADR-0030's definition, holding no state. `LiveRunState` is an
aggregate of the `RunState`, the `BrokerState` and the `ExternalOrderMap`, each
still owned by the module that owns it: no second cursor, no second accounting,
no second order book.

The order is the contract. A fill the venue has already reported reaches the
portfolio **before** the strategy is dispatched, or the strategy reads a book
that does not know about a position it already holds.

**Two ledgers, and a `DUPLICATE` in one says nothing about the other.** This is
the subtlest thing in the release and the first implementation got it wrong.
`BrokerState.executions` answers "has the venue-side bookkeeping recorded this
fill?"; `ExecutionState.reports` answers "has the *portfolio* booked it?". Both
are keyed by `execution_id` and both refuse their own repeat — and a fill in one
and not the other is the normal case, because `poll_executions` applies every
fill it fetches before returning it. Gating the portfolio on the broker layer's
answer silently dropped every live fill. A **break** stops a fill now; a
duplicate does not.

**The venue binding is durable.** `BrokerState` and `ExternalOrderMap` had no
snapshot at all through v2.15, so a live run that stopped forgot which of its
orders the venue held — and the duplicate-submission gate was worth exactly as
much as that mapping's durability, which across a process boundary was zero.
`alphalab.broker.snapshot` and `alphalab.runtime.live_snapshot` close it in two
nested envelopes, so a backtest payload is byte-identical to one written before.

**The lifecycle join.** `alphalab.lifecycle`'s own docstring ended "and the two
are joined by the caller", which meant nothing checked that a run was serving the
version an environment actually had live. `alphalab.lifecycle.execution.run_plan`
resolves a deployment into what should run and `authorize_run` refuses a run that
would serve anything else. A query with a refusal, not a second runtime: it names
no runtime type at all.

## 2. Approval, RBAC and audit

ADR-0018, implemented as recorded. Before it, the lifecycle's audit trail
answered *what* changed and *when* and was silent on *who*, while
`alphalab.enterprise` held a complete RBAC implementation with thirty-two tests
and — by `git grep` — **zero production consumers**.

* **`Governance(enterprise, actor_id, approval_required_in)`** is the required
  second argument of `promote_strategy_version`, `deploy_strategy_version`,
  `rollback_environment`, `retire_strategy_version` and the new
  `approve_deployment`. It has no default: an optional one that skipped the check
  would be the alternative ADR-0018 rejected as "a gate anyone can bypass by
  calling the function directly".
* **The actor reaches both records** — `StrategyPromotionRecord.actor_id` and
  `DeploymentRecord.actor_id` — so the deployment ledger, which the lifecycle
  calls "the only answer to what is live", also answers who put it there.
* **Approval enforces separation of duties.** An approval names an exact
  `(name, version, environment)`, and one granted by the deployer does not count.
* **`governance_log`** reads the three append-only records the lifecycle already
  keeps, and maintains no fourth.
* **Two logs, one authority each.** Nothing writes into `enterprise.AuditEvent`
  and no governance entry point returns an `EnterpriseState`.

**An act no principal requested records no actor.** An incumbent archived because
a replacement displaced it was not archived *by* anyone; `actor_id=""` is the
honest answer. **A rollback needs no approval** — it returns an environment to a
version that already passed whatever gate was in force, and a control that stops
a firm taking a bad release down is not a control.

## 3. True FX / multi-currency

Four ADRs deferred to "the release that supplies the rate source". The shape of
what it supplies is decided by ADR-0020's rejected alternative: *"A configured
rate is an invented one, and a figure derived from it is exactly as wrong as the
figure being removed, with the added cost of looking authoritative."*

`alphalab.portfolio.fx` therefore holds **supplied** rates only. A rate requires
a source and an `as_of`; a non-positive rate, a currency against itself, and two
quotes for one pair are each refused. There is **no triangulation and no implicit
inversion** — EUR/USD at 1.10 does not make USD/EUR `1/1.10`, and
`with_inverses()` marks what it mints as `derived`. A **stale** rate is refused
rather than used.

**The rule did not fork.** `assert_single_currency_book` is still the one
implementation; a currency it can convert stopped being one it must refuse, and a
pair it was given no rate for still is — naming which pair. **The fast path is
untouched**: a homogeneous book takes the same code it always did, so the +1.78%
ADR-0028 measured for guarding the component sums is paid by nobody.

A converted valuation **records every conversion it performed**. ADR-0020 removed
a number in no currency; a number in a currency the book is not wholly in, with
no statement of how it got there, would be the same defect wearing a rate.

**The settlement boundary does not move, and the reason is now sharper.**
ADR-0028 refused a permissive mode because a mixed book would make the next
snapshot raise, or because converting "is FX, and it is deferred". FX removes the
first objection and not the second: `realized_pnl` and `commission_paid` are
single cumulative scalars naming no currency, so a run that *traded* two would
sum them across both. v2.16 closes the **valuation** gap the documentation
claimed and names the four blockers before settlement-level multi-currency.

## 4. The refactor audit

A deliberate search for the internal problems that would make AlphaLab worth
refactoring immediately after declaring v3.0 stable. Twenty-one findings, each
classified as **required before v3.0**, **valid current design**, or **optional
post-v3.0 evolution**. Nothing left as "maybe". See ADR-0032.

Six were required and are fixed. Two existed because a previous release named a
defect class correctly and fixed only some of its instances:

* **The strategy dispatcher identified market events by class *name*.**
  `alphalab.live.events.TickReceived` — a different class with a different
  payload — was routed to `on_tick`, and the resulting `AttributeError` was
  reported as a **FAILED strategy**. Routing now matches module *and* name, which
  identifies a class exactly. It is deliberately not `isinstance`: ADR-0016
  decision 3 forbids `alphalab.strategy` any dependency on `alphalab.market`, and
  importing the canonical types was tried and caught by that boundary's test.
* **Four of six `StrategyContext` protocols were still decorative.** ADR-0031
  fixed `history` and `universe` and left the four the pipeline had populated
  since v2.10 empty, with fifteen construction sites still passing `object()`.
  Under `mypy --strict` a strategy could write `context.history.bars(asset)` and
  could **not** write `context.portfolio.cash("USD")`.
* **`benchmarks/benchmark_workbench.py` had never run** — it crashed on iteration
  0 at every tag back to v2.14 — and three production defects were underneath:
  a rendered tab whose identifier no view exposed, a per-delegation scan of the
  whole Studio event log matching a class name, and pre-v2.1 quadratic
  accumulation in both packages. It now completes 100,000 cycles in ~3.1s.
* **A portfolio optimizer returned silently wrong allocations** for mismatched
  inputs, and **`ARCHITECTURE.md` listed two gaps v2.3 had closed**.

Eleven findings are recorded as **intentional**, each pinned by a regression
test; four as **future evolution**, deliberately not pulled in.

## Added

* `alphalab.runtime.live` — `LiveSession`, `LiveRunState`, `live_health`.
* `alphalab.runtime.live_snapshot`, `alphalab.broker.snapshot`.
* `alphalab.lifecycle.execution` — `run_plan`, `authorize_run`.
* `alphalab.lifecycle.governance` — `Governance`, five permissions,
  `ApprovalRecord`, `governance_log`; `approve_deployment`.
* `alphalab.portfolio.fx` — `FxRate`, `FxRates`, `FxConversion`, `NO_RATES`.
* `alphalab.workbench.views.active_tab`; the six `No*` strategy-context null
  objects; members on four `StrategyContext` protocols.
* `docs/ADR/0032-...md` and `docs/ADR/0033-...md`.

## Changed — breaking

* **`governance` is the required second argument** of the four governed lifecycle
  entry points.
* **`LIFECYCLE_SNAPSHOT_SCHEMA` 1 → 2.** A version 1 payload is refused, never
  read: ADR-0018 rejected optional decoding at schema 1 because it gives one
  version two shapes. `DEFAULT_SCHEMA_VERSION` stayed at 1.
* A class merely *named* `TickReceived` / `QuoteReceived` / `TradeReceived` no
  longer reaches a strategy hook.
* `optimize_maximum_sharpe` / `optimize_minimum_variance` /
  `optimize_inverse_volatility` raise `OptimizationError` on a shape mismatch or
  a missing volatility.
* `StrategyStudioState`, `Project` and `WorkbenchState` hold `PersistentMap` and
  `AppendOnlyLog`.

## Tests

`pytest -q` -> **3767 passed, 2 skipped** (baseline 3580). `ruff check`,
`ruff format --check` and `mypy .` (1003 files) clean. All 13 examples run and
all 50 benchmarks pass.

New suites: `test_live_session.py` (25), `test_integrated_runtime.py` (7),
`test_lifecycle_governance.py` (32), `test_fx_valuation.py` (39),
`test_v216_capabilities.py` (3), `test_strategy_event_routing.py` (13),
`test_strategy_context_contracts.py` (30), `test_workbench_delegation.py` (13),
`test_optimizer_inputs_are_refused.py` (14),
`test_shared_names_stay_distinct.py` (14).

---

## Appendix: the refactor audit in full

Section 4 above summarises it. This is the record, written when the audit
completed and kept because the reasoning is the point.

**The refactor audit: every known structural defect classified, six fixed.**

The audit is a deliberate search for the internal problems that would make AlphaLab
worth refactoring *immediately after* declaring v3.0 stable. Twenty-one findings,
each classified as exactly one of **required before v3.0**, **valid current
design**, or **optional post-v3.0 evolution**. Nothing left as "maybe". See
ADR-0032.

Two of the six required fixes existed because a previous release named a defect
class correctly and fixed only some of its instances. That is the pattern the
audit was most useful for: not an unknown problem, but a known one applied
unevenly.

## Fixed — required before v3.0

### The strategy dispatcher identified market events by class *name*

`alphalab.strategy.dispatcher` selected four of its seven hooks with
`type(event).__name__ == "TickReceived"` and three siblings, under a comment
that began "Assuming generic market events differentiate via class type or
structure". Three packages here define a class by one of those names.

`alphalab.live.events.TickReceived` carries `provider_id` / `symbol` /
`tick_type` where the canonical event carries a `tick`. It was routed to
`on_tick`, the strategy read `event.tick`, and the resulting `AttributeError`
was caught by the dispatcher and reported as a **FAILED strategy** — blamed for
a routing mistake the framework made. `alphalab.marketdata.events` collided on
two more names.

Routing now matches the module *and* the name, which identifies a class exactly.
It is not `isinstance`: ADR-0016 decision 3 forbids `alphalab.strategy` any
dependency on `alphalab.market`, an existing regression test enforces it, and
importing the canonical types was tried and caught. `test_strategy_event_routing.py`
checks the name table against the real types class by class, through the layer
that may import both, so it cannot drift.

`BookUpdated` and `SnapshotCreated` reach no hook, and that is now a stated
boundary with a reason rather than an omission. `StrategyProtocol.on_quote`'s
docstring claimed "Top-of-Book **or L2** quote update" and was corrected.

### Four of six `StrategyContext` protocols were still decorative

ADR-0031 named this defect precisely — a field whose protocol "declares no
methods, and every construction site in the repository passes `object()`" — and
fixed `history` and `universe`. `PortfolioSnapshotProtocol`,
`MarketViewProtocol`, `RiskViewProtocol` and `OrderFacadeProtocol`, the four the
pipeline has populated since v2.10, were left empty, and fifteen construction
sites still passed `object()`.

AlphaLab ships `py.typed`, so this reached every user: under `mypy --strict` a
strategy could write `context.history.bars(asset)` and could **not** write
`context.portfolio.cash("USD")` — *"PortfolioSnapshotProtocol has no attribute
cash"* — for the one surface ADR-0026 exists to supply.

The four protocols now declare what `alphalab.runtime.context_views` implements.
`NoPortfolio`, `NoMarket`, `NoRiskView` and `NoOrders` replace every `object()`,
following `NoHistory` / `NoUniverse`: they answer "nothing" and say so through
`available`, so *no portfolio was supplied* stays distinguishable from *the book
is empty*. All six null objects are exported from `alphalab.strategy`.

### `benchmarks/benchmark_workbench.py` had never run

It crashed on its first iteration with `WorkbenchValidationError: Tab 'bt-BT-0'
is not open.`, identically at every tag back to v2.14. The benchmark was wrong,
and it had no better option — three production defects were underneath it.

* **The rendered tab could not be named.** `WorkbenchEngine.run_backtest` opens
  a tab named after the `result_id` Strategy Studio *mints*, not the
  `backtest_id` the caller passed. `Tab.is_active` existed and every transition
  maintained it, but no view exposed it. **`alphalab.workbench.views.active_tab`**
  is that missing surface.
* **Finding Studio's result scanned the whole event log** and matched a class
  name — O(events) per delegation. It now reads only the events that call
  appended, by type.
* **Both packages accumulated the pre-v2.1 way.** `(*state.events, evt)`,
  `dict(state.backtest_results)` and `(*proj.backtests, config)`: three
  quadratic terms in one loop, throughput halving on every doubling.
  `StrategyStudioState`, `Project` and `WorkbenchState` now use
  `AppendOnlyLog` and `PersistentMap` — the containers v2.1 and v2.2 introduced
  for exactly this and that these packages never took. No new mechanism.

A fourth defect surfaced while fixing the first, and had been unobservable for
the same reason: `WorkbenchManager.close_project` could leave pinned tabs with
**no active tab**. Every transition now maintains the invariant.

| Workload | Before | After |
| --- | --- | --- |
| `benchmark_workbench` (100,000 UI cycles) | crashed on iteration 0 | **3.1s, 32,132 cycles/sec** |
| Workbench session scaling (2x workload) | ~3.2x | **~2.1x** |
| `benchmark_strategy_studio` (10,000 backtests) | 0.76s | **0.12s** |

All 50 benchmarks now pass; 49 did before. The benchmark measures the same thing
it always claimed to, at the workload it always declared.

### A portfolio optimizer returned silently wrong allocations

Three ways of passing inconsistent arguments to
`alphalab.portfolio_optimizer.optimizer` produced a portfolio instead of an
error:

* `optimize_maximum_sharpe(("A","B","C"), (0.1, 0.2), cov_3x3)` returned weights
  for three assets. The matrix-vector product iterates the *vector*, so the
  third expected return and the third covariance column were dropped and C came
  out at exactly zero weight — an allocation decision made by a length mismatch.
* `optimize_inverse_volatility` read `volatilities.get(symbol, 1.0)`, so an
  asset missing from the mapping was sized as though its volatility were 1.0 —
  beside 10%-vol assets, a tenth of its proper weight.
* A covariance matrix of the wrong shape raised `IndexError`, not the package's
  documented `OptimizationError`.

All three are refusals now. The inversion itself was examined and deliberately
left alone: elimination without partial pivoting is the standard backward-stable
choice for the symmetric positive-definite matrices a covariance matrix is, a
search over 20,000 near-degenerate cases found no material inaccuracy, and a
singular matrix is already refused.

### `ARCHITECTURE.md` listed gaps that v2.3 had closed

The "Known gaps" list still said market-data model convergence was "not done"
and `broker` / `brokers` overlapped, both "deferred to v2.3" — three releases
after v2.3 closed them, with regression tests asserting the identities. Corrected,
under the same release criterion that made the v2.15 docstring truth-up a
blocker.

## Documented — valid current design

Eleven findings that look like duplication or a layer violation and are not,
each pinned by `tests/regression/test_shared_names_stay_distinct.py` so a future "unification"
has to break an assertion and read a reason first: the two `OrderBook`s (my
working orders, the market's depth), the two `PortfolioEngine`s (accounting,
construction), `optimizer` vs `portfolio_optimizer`, the converged
`broker` / `brokers` boundary, the two matrix inversions, the three deprecated
zero-consumer packages, the absent CLI, the absent vectorized layer, and
`lifecycle` taking `studio`'s `StrategyDefinition` rather than defining a second
strategy declaration.

## Recorded — optional post-v3.0 evolution, not implemented here

Ten standalone packages still accumulate quadratically (measured: `scheduler`
grows at ~3.2–3.8x per doubling); `ResearchPayload.parameters` is a `dict` on a
frozen dataclass; `brokers.protocol.BrokerProtocol` shares a name with the
canonical boundary; `Dispatcher.dispatch_event` still takes `event: Any`. None
is on the execution path and no documented claim is false. See ADR-0032.

## Added

* `alphalab.workbench.views.active_tab` — the focused tab, or `None`.
* `NoPortfolio`, `NoMarket`, `NoRiskView`, `NoOrders`, and the previously
  internal `NoHistory` / `NoUniverse`, exported from `alphalab.strategy`.
* Declared members on `PortfolioSnapshotProtocol`, `MarketViewProtocol`,
  `RiskViewProtocol` and `OrderFacadeProtocol`.
* `docs/ADR/0032-the-refactor-audit-and-the-v3-condition.md`.

## Changed

* `StrategyStudioState`, `Project` and `WorkbenchState` hold `PersistentMap` and
  `AppendOnlyLog` where they held `dict` and `tuple`. Both are `Mapping` and
  `Sequence`; value semantics and full history are unchanged.

## Tests

`pytest -q` -> **3662 passed, 2 skipped** (baseline 3580). `ruff check`,
`ruff format --check` and `mypy .` (992 files) clean. All 13 examples run and
all 50 benchmarks pass.

New regression suites: `test_strategy_event_routing.py` (13),
`test_strategy_context_contracts.py` (30), `test_workbench_delegation.py` (13),
`test_optimizer_inputs_are_refused.py` (14),
`test_shared_names_stay_distinct.py` (14).

---

# [2.15.0] - 2026-09-13

**Real Execution, Streaming Market Data, Artifact Storage, and the Two
Completed Boundaries.**

Five capabilities had a contract and nothing behind it. Each of them is now
implemented, and each landed on a boundary that already existed rather than
beside one.

The repository said so itself, in five places:

* `ARCHITECTURE.md`: "**AlphaLab does not support live trading.** It supports
  the adapter contract a live venue would be reached through." Routing,
  reconciliation, pre-trade gates and idempotent submission were all real and
  tested. There was no way to reach a venue.
* `market/provider.py`: "It is a **history** source ... It does not poll,
  subscribe, reconnect or stream." `binanceClient.subscribe` was a documented
  no-op. There was no socket anywhere on the canonical path.
* `ArtifactRef`: "**AlphaLab never reads, writes or hashes those bytes**; there
  is no object store here." The `checksum` field was a place for a number nobody
  computed, on a type whose docstring promised it "lets a later reader detect
  that the file behind a version changed".
* `classify_instrument`: a reclassification happened "silently, with no refusal
  and no event", so the registry could say *what* an instrument was classified
  as and never *who said so, from what source, or as of when*.
* `HistoryAccessorProtocol` and `UniverseProtocol` declared **no methods at
  all**, and every construction site in the repository passed `object()`.

See **ADR-0031**.

## Added

- **One TLS policy, for every outbound connection.** `alphalab.common.tls` —
  `MINIMUM_TLS_VERSION` and `tls_context()`. Three call sites reach somebody
  else's server over TLS (the WebSocket feed, the venue transport, the provider
  REST client) and all three share this definition, because a security floor
  written down three times is a floor that can drift.

  It exists because `ssl.create_default_context()` **does not pin a protocol
  floor**: it sets neither `OP_NO_TLSv1` nor `OP_NO_TLSv1_1`, and
  `minimum_version` comes back as whatever the host's OpenSSL build and
  `openssl.cnf` impose. On one machine that is TLS 1.2; on a host with a
  permissive crypto policy the same code negotiates TLS 1.0, which RFC 8996
  deprecated. A guarantee that holds because of how a machine happens to be
  configured is not a guarantee the code has — the same reason
  `UnresolvedIdentity` is refused at the source boundary rather than trusted to
  be configured correctly.

  Certificate verification (`CERT_REQUIRED`) and hostname checking
  (`check_hostname=True`) are `create_default_context`'s and are preserved
  exactly. The floor is TLS 1.2 rather than 1.3 because 1.3 is not yet universal
  at trading venues, and refusing a 1.2-only venue would be a functional break
  rather than a security gain. No ceiling is set, so 1.3 is used whenever the
  peer offers it. There is deliberately **no parameter that lowers the floor**
  and no retry-on-older-protocol fallback, which is the downgrade an attacker
  would try to provoke.

  It lives in `common` because it belongs to neither `broker` nor `marketdata`,
  and importing it across would have added a `broker → marketdata` dependency
  for a security constant. The package graph is unchanged: `common → []`,
  `broker → [common, core]`, `marketdata → [common, data]`, no cycles.
- **A venue transport.** `alphalab.broker.transport` — `VenueTransport`,
  `HttpVenueTransport` (authenticated JSON-over-HTTP with HMAC-SHA256 request
  signing, standard library only), `VenueResponse` and `VenueCredentials`.
  Deliberately a *separate* seam from `marketdata.transport`, which is
  unauthenticated and GET-only: order submission needs a body, a method that is
  not GET, a signature, and the status code. **The status code is the
  load-bearing part** — a venue refusing an order (`4xx`) and a venue being
  unreachable (a socket error) are different facts, and only the absence of an
  answer raises. An `https://` venue is reached with an **explicit** TLS
  context, so an order submission never inherits its security properties from a
  machine's configuration.
- **A real broker adapter.** `alphalab.broker.venue.RestVenueBroker`, a full
  `BrokerProtocol`: submit, acknowledge, reject, cancel, replace, order status,
  fill polling, account, positions, connect/heartbeat/disconnect, and recovery
  by reading an order back after a lost response. `runtime.broker_routing`
  routes to it without knowing which adapter it has, and its fills reach the
  portfolio through `apply_execution_report` — the function a simulated fill
  takes.
- **A WebSocket client.** `alphalab.marketdata.websocket` — RFC 6455 over the
  standard library, with `wss://` connections made through the shared TLS
  policy: opening handshake with the accept token **verified** rather
  than merely required, all three payload-length forms, client-side masking,
  continuation reassembly, ping/pong, and the closing handshake.
- **A streaming market-data source.** `alphalab.market.stream.StreamingSource`,
  which **is a `MarketDataSource` and nothing more** — `records()` already
  returned an iterator, so a generator pulling a live socket satisfies it with
  no new abstraction and `TradingSession.run` needed no change. Connection,
  subscription, incremental events, per-symbol sequence deduplication, gap
  counting, malformed-message handling, staleness, heartbeat-based liveness
  detection, reconnect-and-resubscribe, bounded frame buffering and graceful
  shutdown.
- **Artifact storage.** `alphalab.model_registry.artifact_store` —
  `ArtifactStore`, `FileArtifactStore` (atomic writes, sharded, digest verified
  before anything is returned) and `MemoryArtifactStore` (the deterministic
  double, constructed **by name**). **Identity is the content**: an artifact is
  addressed by the SHA-256 of its bytes, so storing identical bytes twice is one
  artifact, two environments agree with no shared database, and a reference
  cannot name bytes that hash to something else. It produces
  `model_registry`'s own `ArtifactRef` — no second reference type.
- **Classification provenance.** `alphalab.instrument.classification` —
  `SectorClassification` (label, `source`, `as_of`) and an append-only
  `ClassificationHistory` per instrument. `classify_instrument` and
  `classify_instruments` gained optional `source` and `as_of`;
  `classification_of`, `classification_history` and `sector_as_of` read them.
  **Append-only, because overwriting is mutable historical attribution** — a
  correction is a new fact, and the entry it corrects stays readable.
- **A durable security master.** `alphalab.instrument.snapshot` —
  `capture` / `restore` / `from_primitives` for the registry. This supersedes
  ADR-0027's "persisting the `InstrumentRegistry`" non-goal, and **only** that
  entry: the premise changed, because the classification history is a record of
  a *sequence of acts* and is not re-derivable from a declaration file.
  `asset_id` is re-derived on restore, never read, so a snapshot cannot assert
  an identity.
- **`StrategyContext.history`.** `runtime.context_views.HistoryView` — a
  clock-bounded accessor over the market engine's event log, with `as_of` taken
  from the **event** and never a wall clock. Look-ahead safety rests on a
  structural fact (the log holds only already-published events) *and* an
  enforced filter. Constructing it is O(1); reading walks backwards and stops at
  `limit`.
- **`StrategyContext.universe`.** `runtime.context_views.UniverseView` —
  membership is the **instrument registry**, answering ADR-0026's deferred
  semantic question. A run without one has an empty universe and `configured`
  says why; the priced assets are a different question and keep their own name.
  `universe.sector()` is where the security master reaches the strategy.

## Changed

- `_populate_context` overlays **six** fields instead of four. Both additions
  are references to state already in scope, measured at nothing across the
  execution, backtesting and runtime benchmarks against v2.14.
- `StrategyContext.history` and `.universe` gained defaults (`NoHistory`,
  `NoUniverse`), so a caller building a context by hand omits them rather than
  fabricating a placeholder — and gets a value that **says** it supplies nothing
  instead of one that quietly looks empty. Every construction site in the
  repository was updated.
- `HistoryAccessorProtocol` and `UniverseProtocol` declare real methods.
- **`pyproject.toml`, `alphalab.common.version` and the metadata test all said
  `2.13.0`**, and are now `2.15.0`. v2.14.0 shipped without a version bump; this
  corrects it rather than carrying it forward.

## Fixed

- **`HttpTransport.get` inherited its TLS floor from the host.** The provider
  REST client behind `ProviderHistorySource` called `urllib.request.urlopen`
  without an explicit `context=`, so market-data fetches took whatever minimum
  protocol the machine's OpenSSL allowed — pre-existing since v1.39.0. It now
  uses `alphalab.common.tls`. No static analyser could have found this: urllib
  builds the context internally, so there is no `SSLContext` construction in
  AlphaLab's dataflow to flag. The repo-wide regression guard found it.
- `StreamingSource` caught `TypeError`/`ValueError` but not `KeyError`, so a
  venue message *missing* a field would propagate out of the generator and kill
  a live session holding positions. Now counted as malformed like every other
  unusable message.
- Recording provenance initially made `classify_instrument(reg, id, None)` on an
  *unclassified* instrument stop being a no-op, putting a "sector withdrawn"
  entry in the audit trail of an instrument that never had one. The v2.11 no-op
  is preserved explicitly.
- `test_lifecycle_registry_complexity.py`'s seven timing assertions took a
  single sample each, and the smallest was ~4ms — small enough for one garbage
  collection to tip its ratio past the bar. They now measure best-of-3. **No
  threshold changed**: a deliberately quadratic implementation still measures
  13.9x against the 8.0x bar. Measured directly, the path under test is flat at
  ~4µs/item from 1,000 to 32,000 names.

## Explicitly not in this release

- Verification against any commercial venue. This environment has no network
  egress and holds no vendor credentials; both transports say so in their own
  docstrings. The protocol is proven against local servers that verify
  signatures, timestamp windows, idempotency keys and WebSocket accept tokens.
  **The connectivity exists; the vendor integration does not.**
- Any named vendor's request shapes. The `alphalab.integrations` clients
  (Alpaca, IB, Zerodha) remain canned-response stubs.
- Allocation visibility in the strategy context — ADR-0026 refuses it
  permanently, and adding it to complete a checklist would duplicate
  `AllocationEngine`'s authority.
- Overlaying `StrategyContext.clock`, which ADR-0026 recorded as the caller's.
- Methods on `MarketViewProtocol`, `PortfolioSnapshotProtocol`,
  `RiskViewProtocol` or `OrderFacadeProtocol`. They remain method-less as v2.10
  left them; the two that were *blocking a capability* are the two that changed.
- Removal of the deprecated `alphalab.production` and legacy `persistence`
  modules. Those are v3.0's.
- A supervised live *process*. A live driver over `RunEngine` is what ADR-0030
  anticipates; assembling one is not this release.

## Verification

`ruff check`, `ruff format --check`, `mypy .` (987 files, strict) and
`python -m build` all clean. **3,580 tests pass** against v2.14.0's 3,335,
stable over consecutive full-suite runs. All 13 examples run. Execution,
backtesting and runtime benchmarks are within run-to-run noise of v2.14.0.

35 of those tests are TLS regression guards
(`tests/regression/test_websocket_tls_policy.py`,
`tests/regression/test_venue_transport_tls_policy.py`), including two repo-wide
AST checks — every `SSLContext` must pin a `minimum_version`, and every
`urlopen` must pass an explicit `context`. Each guard was verified to **fail
when its fix is removed**, and the load-bearing ones monkeypatch the *ambient*
default to something weak and assert the floor holds anyway, which is what
distinguishes a real fix from one that merely looks right on a well-configured
machine.

---

# [2.14.0] - 2026-09-12

**Run Runtime Unification.**

*This entry was reconstructed in v2.15 from the v2.14.0 tag, ADR-0030 and the
release diff. v2.14.0 shipped without a changelog entry and without a version
bump — both are recorded here rather than left as a gap in the history.*

The run layer was implemented twice. `TradingSession.resume` and
`BacktestEngine.resume` were character-identical, and two owners of one
determinism contract is a contract that can drift. See **ADR-0030**.

## Added

- **`alphalab.runtime.run`** — `RunEngine` over `RunState`, the canonical owner
  of a record-driven run: the cursor, what it declined to act on, what each
  record produced, and the identifier scope a stopped run continues in.
  `RunConfig`, `RunStep` and `SkippedRecord` come with it.
- **`alphalab.runtime.run_snapshot`** — `RUN_SNAPSHOT_SCHEMA = 1`, `RunSnapshot`,
  `RunObjects` and the `capture` / `restore` / `from_primitives` trio, replacing
  the two per-driver snapshot modules.

## Changed

- A **driver** is now anything that decides which record comes next and what
  clock reading judges it. `TradingSession`, `BacktestEngine` and
  `ReplayBacktest` are the three, and they hold no run state of their own.
- `RunState.source_id` gives a backtest's dataset identity a home on the
  captured state, which it previously lacked.

## Removed

- `SessionState`, `SessionConfig`, `BacktestState`, `BacktestConfig` and
  `BacktestStep`; `runtime.session_snapshot`, `backtesting.snapshot` and
  `backtesting.config`; `SESSION_SNAPSHOT_SCHEMA` and
  `BACKTEST_SNAPSHOT_SCHEMA`.

## Deprecated

- **`alphalab.production`** — removed in v3.0. It names itself after a runtime
  it does not run and records a durability it does not provide; measured at
  v2.13 it had zero production importers.

---

# [2.13.0] - 2026-09-12

**Durable Run State + the Run-State Store.**

`capture` / `restore` have covered `ExecutionPipelineState`, `SessionState` and
`BacktestState` since v2.9, and `StrategyStateProtocol` closed the last
precondition in v2.10. A run could be described completely and rebuilt exactly.
It could not be **put anywhere**.

Measured at v2.12: zero filesystem calls in `alphalab` — the one I/O call in the
package is `urllib.urlopen`, in `marketdata/transport.py`. `MemoryStorage` was
the only `PersistenceProtocol` implementation, held everything in memory, had no
run identity, no sequence, and no decoder for its own `PersistenceState`. And no
production module imported `runtime.snapshot`, `runtime.session_snapshot` or
`backtesting.snapshot` at all: their only callers were tests. The one object in
the tree that claimed to own recovery — `production.Checkpoint` with
`RecoveryEngine` — held six opaque caller-supplied strings and restored nothing.

The gap was not serialization, not schema, and not a missing restore boundary.
**Durability had no owner.**

There was also a defect underneath it. `MemoryStorage._create_id()` calls
`new_id()`, which reads the ambient identifier source, so every storage
operation drew from whatever stream was installed. Inside a run's `id_scope`
that is the *run's* stream: one `save_snapshot` plus one `append_event` advanced
`draws` **0 → 2**, and the two identifiers it took were the run's own next two —
stamped onto a `SnapshotSaved` and an `EventAppended` while the run skipped to
its fourth and fifth. `load_snapshot` drew as well, so a durable store built on
that protocol could not have *read a run back* without changing it.

The shape of the answer was already in the repository. `marketdata/transport.py`
diagnosed the same class of problem — "silently fake data with no seam to ever
make it real" — and answered it with a narrow `Transport` protocol, a real
`HttpTransport`, and a `StaticTransport` a caller constructs by name. This is
that shape, applied to persistence. See **ADR-0029**.

## Added

- **`RunStateStore`** — the one owner of durable run state, in
  `alphalab.persistence`. Four methods over `(run_id, sequence) → payload`:
  `put`, `get`, `latest`, `list_runs`. **Payload-agnostic**: it moves a `str`,
  imports no snapshot, runtime, session, backtesting, portfolio, OMS or strategy
  module, and never decodes what it holds. There is no `delete`, no `append`, no
  query language and no transaction, because nothing here needs one.
- **`FileRunStateStore`** — the real local backend. Standard library only, so
  `dependencies = []` stays true. Writes atomically (temporary file in the
  destination directory, `fsync`, then `os.replace`), records a SHA-256 digest
  and **verifies it before any decoder runs**. The root must already exist and be
  writable; a missing root, a root that is a file, and an unwritable root each
  **raise**. It never falls back to memory.
- **`MemoryRunStateStore`** — an explicitly named deterministic double, the
  `StaticTransport` of this boundary. Same contract, same refusals, same
  ordering. A caller constructs it by name; nothing selects it automatically and
  `FileRunStateStore` never degrades into it.
- **`RunStateRef(run_id, sequence)`** — an **identity, not a location**. Renders
  `run_id@sequence` on the separator `alphalab.lifecycle.identity` already uses,
  and refuses an empty `run_id`, a `run_id` containing `"@"`, a non-integer
  sequence and a negative one. It carries no URI, no path, no checksum and no
  byte size: a backend's addressing stays inside that backend, so a second
  backend would change no caller. Deliberately *not* shaped like `ArtifactRef`,
  which describes bytes AlphaLab never holds.
- **`RUN_STATE_ENVELOPE_SCHEMA = 1`** — the only new schema constant. A
  module-local literal, versioning what the store records *about* a payload and
  nothing inside it.
- **`examples/13_durable_run_state.py`** — a run stopping after five records,
  crossing to a separate interpreter, finishing six more, and comparing
  byte-identical against a run that never stopped.
- **ADR-0029**, *The Run-State Store and the Durability Boundary*.

## Changed

- **Persistence draws no identifier from the run's stream.** A complete
  `put` / `get` cycle inside `id_scope` advances `draws` by **exactly zero** —
  on both backends, on every method in isolation, and on the refusal paths where
  a stray identifier would otherwise hide. Neither store module calls `new_id`,
  and a structural test keeps it that way. This is what makes a mid-run
  checkpoint safe in a seeded run.
- **`alphalab.common.serialization` resolves `__serializable__` on the type**
  rather than through a `runtime_checkable` protocol check. Profiled on a
  1,600-event snapshot, that one `isinstance` was ~60% of `serialize`:
  1,827,034 calls into `inspect._shadowed_dict` and 557,008 protocol checks,
  against 0.19s actually spent encoding JSON. Measured against the pre-change
  implementation kept verbatim as an oracle: **698.0 ms → 188.8 ms, 3.70×, for a
  byte-identical 6,706,275-byte payload.** Every snapshot in the repository is
  faster — portfolio, OMS, allocation, lifecycle, pipeline, session, backtest.
  The one behavioural difference is recorded rather than glossed: a
  `__serializable__` set on an *instance* is no longer consulted, only one
  declared on a class. Dunder lookup conventionally goes through the type, and
  all seven declarations here are `def` at class scope.
- **`production.Checkpoint` and `RecoveryEngine` now say what they do.**
  Documentation only — no behaviour change, nothing removed, nothing aliased.
  Their state fields are opaque strings this package never decodes, `recover`
  restores nothing, and the docstrings now name `RunStateStore` as the boundary
  that does.

## Deprecated

- **The original nine-module persistence store** — `protocol`, `storage`,
  `state`, `engine`, `adapter`, `snapshot`, `views`, `validation`, `events` —
  is deprecated, and **removed in v3.0**. Measured at v2.12 it had **zero**
  production importers.
- **The codec spine remains canonical and is untouched**: `serializer`,
  `decode` and `exceptions` are imported by all seven snapshot modules and are
  not deprecated.
- The notice fires on **use of a deprecated name through the package**, not on
  importing the package, through the PEP 562 mechanism
  `alphalab.common.CommonEvent` already uses and for the same reason: an
  import-time warning here would fire on every consumer of `serialize` and
  `decode` — that is, on the whole execution path — to deprecate names that path
  never touches. `from alphalab.persistence.storage import MemoryStorage` stays
  silent.
- **Nothing is removed in v2.13.** These modules keep their behaviour, their
  signatures and their tests for the whole of v2.x. `RunStateStore` is not a
  renamed `PersistenceProtocol`: it has different addressing, a different unit
  of storage and a different identifier contract.

## Fixed

- Nothing. No defect in shipped behaviour was corrected by this release. The
  identifier consumption described above is a property of the deprecated store,
  which is characterized rather than repaired — see
  `tests/regression/test_persistence_draws_from_the_run_stream.py`.

## Compatibility

**No existing schema constant moves.** `PIPELINE_SNAPSHOT_SCHEMA` stays 2;
`SESSION_SNAPSHOT_SCHEMA`, `BACKTEST_SNAPSHOT_SCHEMA`,
`ALLOCATION_SNAPSHOT_SCHEMA`, `OMS_SNAPSHOT_SCHEMA` and
`LIFECYCLE_SNAPSHOT_SCHEMA` stay 1; `PORTFOLIO_SNAPSHOT_SCHEMA` stays 2;
`DEFAULT_SCHEMA_VERSION` stays 1. Only `RUN_STATE_ENVELOPE_SCHEMA = 1` is new,
and it is new, so it has nothing to be compatible with — no legacy shape, no
migration, no "missing means 1".

A v2.12 serialized payload is byte-for-byte what the store holds: the envelope
wraps, and never rewrites, re-encodes or reorders. **The run identity is not
persisted in any captured state** — it is supplied by the caller at `put`, which
is what keeps every snapshot schema still. `ArtifactRef` is unchanged and not
moved. `capture` / `restore` ownership is unchanged: the module that owns a state
still owns its projection, and no state class grew a method.

## Verified

- **Cross-process continuation.** A seeded run processes five of eleven records,
  captures, serializes and stores; a **separate interpreter** — a fresh
  `sys.executable`, not a fork, told everything in JSON and nothing by pickle —
  reads it back, restores against newly constructed runtime objects, resumes and
  processes the remaining six. The final serialized payload is **byte-identical**
  to an uninterrupted eleven-record run. ADR-0023's Class-1 comparison and the
  identifier list are asserted alongside it as failure localization, not as a
  replacement for the oracle.
- **The strategy's memory crosses too.** The workload's strategy trades on the
  parity of a counter it owns, so a child that restored the payload but not the
  strategy state would trade the wrong records and fail loudly rather than pass
  quietly.
- 3,190 tests, `mypy --strict` over 903 source files, `ruff` clean, wheel and
  sdist built and `twine check`ed.

## Explicitly not in this release

**No FX and no multi-currency valuation** — valuing across currencies still needs
a rate source that does not exist here, and v2.12's refusals stand.
**No `ArtifactStore` and no artifact-byte backend** — nothing in the tree
produces artifact bytes; `reporting.export_json`, `export_csv` and
`export_markdown` all return `str`. `ArtifactRef` continues to record where bytes
live without AlphaLab ever reading them.
**No cloud or vendor-specific storage.** **No streaming.** **No live venue
transport.** **No governance/RBAC implementation** — ADR-0018's actor record
stays deferred, because it moves a persisted lifecycle schema and is batched with
ADR-0017's evidence provenance. **No broad runtime rewrite** — the archaeology
established that the final persistence boundary does not require one, and
`ExecutionPipeline`, `TradingSession` and `BacktestEngine` are untouched.

Also deferred, deliberately: replay resumability (`ReplayState` has no snapshot
and the replay cursor drives a second identifier stream); recording live-object
*parameters* rather than class names, which would move the pipeline schema;
trimming the payload, of which 75.2% is engine event logs that no engine
*decision* reads but which ADR-0023's Class 1 includes; and incremental or
event-sourced checkpoints.

**Runtime unification remains the next architectural seam.** ADR-0023 decision 1
records that `SessionState` and `BacktestState` are the layer a future
integrated-runtime release is expected to reshape, and split the snapshot
envelopes so that reshape can move their two constants without versioning the
stable pipeline core. This store is built so that reshape cannot reach it: it
names neither state, holds only a `str`, and is addressed by an identity the
caller supplies.

## Performance

Persistence is an explicit caller action **between completed steps**, never
per event, and v2.13 adds no work to the execution path — no store symbol appears
anywhere in `alphalab.runtime`, `alphalab.backtesting`, `alphalab.strategy` or
`alphalab.replay`, and there is deliberately no append-per-event API. The reason
is measured: one capture plus serialize of a 1,600-event run costs 1.43s against
0.40s for the run itself and produces 13.4MB, so capturing per event would make a
run quadratic in its own length. A run that never persists is bit-for-bit the
v2.12 run.

Checkpoint cost is unchanged as a design position and is linear in state size —
2.9×–3.9× the run that produced it, across 100 to 6,400 events. v2.13 makes
persistence real without making it cheap; the `_convert` change reduces the
constant by 3.70× on every payload.

---

# [2.12.0] - 2026-09-12

**The Currency Authority.**

ADR-0016 made currency one of the four fields `asset_id` is derived from, so the
registry has held an authoritative, immutable answer for every registered
instrument since v2.7: changing a currency derives a different identifier, and
the classification write cannot reach it. The execution path never asked.
`_instruction` stamped `ExecutionPipelineConfig.currency` onto every
`OrderInstruction`, `ExecutionReport.currency` copied it, and
`_apply_report_to_portfolio` booked `Position.currency` from it. A
EUR-registered instrument traded on a USD pipeline produced:

```text
InstrumentRecord("SAP", EQUITY, "XETR", "EUR")   # the registry's answer
ExecutionReport.currency = "USD"                 # from the config
Position.currency        = "USD"                 # a position in no currency
```

No error, no warning, nothing downstream able to tell. ADR-0019 recorded that as
intended, and at the time it was — v2.7 had only just introduced the registry
and nothing on the execution path read it. v2.11 changed that: it threaded the
registry onto the pipeline and read it on the fill path for sector. The distance
between "this run does not know the instrument's currency" and "this run knows
it and ignores it" is the whole of this release.

Two further facts, each verified rather than inferred. `RoutingConfig.currency`
is a **fifth** currency site — ADR-0019 named three, `NormalizationPolicy` is a
fourth — defaulting to `"USD"` and compared to nothing, so a live sell against a
mismatched routing config booked a foreign position and foreign cash silently,
leaving a book whose next valuation raised mid-run. And a mixed book was already
fatal one event later: `_process_requests` snapshots the portfolio at the end of
every event, so funding a second currency onto a running pipeline raises
`MixedCurrencyValuationError` out of `process_quote` immediately.

## Added

- **`SettlementRefusal`**, reported on `ExecutionPipelineResult.settlement_refusals`
  — one per refused request, naming the instrument, its currency, this
  pipeline's settlement currency, and the fix. Derived and never persisted: no
  snapshot carries an `ExecutionPipelineResult`, and the field is appended after
  `valuation` so positional construction keeps working. An aggregated durable
  record in the manner of `UnpricedAsset` would move the pipeline schema, and is
  deferred to the release that moves it anyway.
- **`assert_single_currency_book(cash, positions, base_currency)`** — the one
  implementation of the mixed-book rule, written over the two components so that
  callers holding a ledger and a mapping reach the same rule and the same
  message as callers holding a `PortfolioState`. `assert_single_currency` keeps
  its signature and delegates.
- **ADR-0028**, *Currency Authority and the Settlement Boundary*.

## Changed

- **The instrument's currency decides what a run may trade.** Two seams, because
  there are two questions. **Seam 1**, in `_process_requests`, asks whether this
  run may *trade* an instrument, needs the registry to answer, and **drops** the
  request before the OMS — retiring both allocation ledgers through the existing
  `_retire_dropped_request`, so no reservation leaks and no contribution is
  orphaned. **Seam 2**, in `_apply_report_to_portfolio`, asks whether a report is
  denominated in what the pipeline *settles*, needs no registry, and **raises**
  `RuntimeValidationError`. Neither subsumes the other: with only Seam 2 a
  foreign instrument still slips through carrying the settlement currency, and
  with only Seam 1 a venue report never passes a check at all.
- **The dispositions differ because the vocabularies do.** The pipeline may
  decline to create an order; it may not decline a fill that already happened at
  a venue. This is the distinction `_close_unfilled_order` and `_terminate_order`
  already draw. Raising at Seam 1 would kill a live session over one instrument;
  dropping at Seam 2 would discard a real execution.
- **Seam 1 runs after the existing unpriced check**, so an instrument that is
  both foreign and unpriced is still reported as `REGISTERED_BUT_UNPRICED`.
- **`RoutingConfig.currency` is constrained** at Seam 2. The field and
  `execution_report_from_broker`'s signature are unchanged; the default `"USD"`
  is now a loud refusal on a non-USD pipeline rather than silent corruption.
- **`NAVCalculator.calculate`, `PortfolioValuation.portfolio_value` and
  `_risk_exposure` refuse a mixed book**, discharging ADR-0020 decision 5 —
  earlier than it expected, on measurement rather than on a rate source. That
  also closes `sector_exposure`, which v2.11 added and which bucketed signed
  market value with no regard for what each position traded in.
  `_risk_exposure`'s check folds into the pass it already makes.
- **`long_value` and `short_value` deliberately do not refuse.** They name no
  base currency, return an unlabelled sum, and cannot be told what to refuse
  against — a `Mapping[str, Position]` carries no account. They are components
  of a valuation, not valuations, and their only production caller is `snapshot`,
  which guards first and is the thing that attaches the label. The three-way rule
  — aggregates *and* names a currency, aggregates only, or looks up only — is the
  one the code already followed; v2.12 names it, documents it, and pins it
  against the signatures.
- **Corrected documentation.** "Holding and booking in a foreign currency is
  supported" described `PortfolioEngine` used standalone and was carried as
  though it described a pipeline run, which it never did. A stale ROADMAP bullet
  claiming strategies still do not see the marked portfolio — contradicted by
  v2.10 and by the entry above it — is marked done.

## Not changed

`STRICT_MATCH` is the only settlement rule and there is **no policy object**: a
permissive mode could only book honestly, making the book mixed so the next
snapshot raises, or convert, which is FX. `_instruction` still reads
`config.currency`, because under STRICT_MATCH that *is* the instrument's
currency for anything that trades — `Position.currency` follows
`InstrumentRecord.currency` by an equality the refusal enforces, not by a second
registry read. Nothing is added to `TradeRecord`, `OrderRequest` or `oms.Order`:
currency is an identity input and is already frozen by identity, unlike sector.
`PortfolioEngine` is not narrowed and still books any currency it is given.
`_currency_of` mirrors `_sector_of` exactly — one keyed `record_for`, no scan, no
identifier draw — and the authority is **opt-in**: a run with `instruments=None`
behaves exactly as v2.11.

**No FX rate source, no conversion, no triangulation, no multi-currency
aggregation.** A mixed book is still refused, not valued. `CURRENCY_QUANT` stays
`0.01`. No per-currency breakdown was added to `PortfolioValuationSnapshot`:
under STRICT_MATCH a pipeline book holds one currency and a mixed book is
refused before any figure is computed, so it could only ever restate `equity`.

## Compatibility

Two deliberate behavioural breaks, both correctness fixes: a run that silently
mis-booked a foreign instrument now drops the request, and a venue report
disagreeing with settlement is refused. Every position the first removes was
mislabelled; every book the second prevents was one whose next valuation would
have raised.

Measured against v2.11.0 in a separate checkout, with module provenance asserted
by path:

- all seven snapshot schema constants unchanged — `PIPELINE=2` `READABLE=(1,2)`,
  `SESSION=1`, `BACKTEST=1`, `PORTFOLIO=2`, `OMS=1`, `ALLOCATION=1`;
- a v2.11 payload restores and re-captures byte-identically;
- a single-currency run is byte-identical: same fills, same equity, same
  `sector_exposure`, same serialized snapshot SHA-256;
- identifier draws identical — 10,001 at 1,000 events and 40,001 at 4,000;
- `asset_id` derivation and `canonical_key` unchanged;
- no migration, and no migration framework.

Before implementation, instrumenting `_apply_report_to_portfolio` across the
whole v2.11 suite found **zero** fills where a configured registry's instrument
currency differed from the report currency: no existing test trades a foreign
instrument on a registry-configured pipeline.
`test_the_currency_blind_siblings_are_unchanged_in_this_release` is rewritten,
as its own docstring anticipated.

## Performance

Gate: ≤3% at 1,000 and 4,000 events against v2.11.0, back to back, identifier
draws identical, scaling no worse.

A first pass compared a fresh v2.12 run against a v2.11 number measured earlier
in the session and reported +0.39% at 1,000 events; repeating it minutes later
on identical code gave +3.18%. The swing is the machine, not the change — within
a single tree, run-to-run spread is 2.6% at 1,000 events and 5.1% at 4,000 —
so the figures below come from a properly interleaved A/B instead: one fresh
process per sample, trees alternating and reversing order each round, nine
rounds of five timed runs each, in a separate `v2.11.0` checkout whose module
provenance is asserted by path.

| | v2.11.0 | v2.12.0 | on medians | on minima |
| --- | --- | --- | --- | --- |
| 1,000 events | 321.8 ms | 324.1 ms | **+0.72%** | +1.01% |
| 4,000 events | 1369.3 ms | 1371.6 ms | **+0.17%** | +0.92% |

Both inside the gate, and both smaller than the noise floor of a single
measurement — which is the reason the gate says "back to back". At larger books,
where the guards are `O(positions)`: −0.24% at 200 positions and +0.46% at 1,000.

An optional single-pass `NAVCalculator` rewrite was measured and **deferred**:
the gate passes with margin, so it would be an optimisation outside the
release's theme carrying its own byte-identity risk.

An optional early settlement check inside `route_order` was also deferred, for a
reason found in the signature: it takes a `BrokerState`, a `BrokerProtocol`, an
`OMSOrder`, a timestamp, a mapping and a `RoutingConfig`, and none of them
carries the settlement currency. Giving it one means a new parameter on a public
broker-boundary function, for a check Seam 2 already makes and no caller can skip.

---

# [2.11.0] - 2026-09-07

**The Security Master.**

ADR-0016 N5 excluded `sector` from the canonical instrument key in v2.7, so that
classification could arrive later without re-identifying an instrument and
orphaning every fill recorded against it. Four releases later the field was
still populated by nothing — and, less obviously, still *writable* by nothing:
`register_instrument` refuses a record whose content differs from one it already
holds, and `InstrumentRecord.__eq__` does not ignore `sector`, so a field
documented as mutable raised on every attempt to change it.

Downstream of that, two consumers had been finished and left empty.
`AttributionMetrics.pnl_by_sector` has bucketed correctly since v2.6 — and
refused a `"UNCLASSIFIED"` placeholder — while receiving `sector_id=None` from
every pipeline fill ever produced, because `_trade_record` hardcoded it.
`ExposureStatus.sector_exposure` has been declared, typed, persisted and decoded
for longer than that and was never given a value at all. Meanwhile the registry
that could answer both was already threaded through the whole execution path on
`ExecutionPipelineConfig.instruments`, and was read from exactly one place:
`_classify_unpriced`, a route a healthy run never takes. The authority was
configured and consulted only when something had gone wrong.

## Added

- `alphalab.instrument.registry.classify_instrument(registry, asset_id, sector)`
  and `classify_instruments(registry, classifications)` — the write ADR-0016 N5
  kept the identity key clear for. Module-level functions rather than registry
  methods, because every write in that package is a function and the registry's
  own methods are pure reads. Keyed by `asset_id` rather than by an
  `InstrumentRecord`, which would admit one whose identity fields disagree with
  the registry's — a question with no good answer.
- `alphalab.instrument.record.normalize_sector_label(value, field_name="sector")`
  — strips surrounding whitespace and refuses an empty, whitespace-only,
  control-character or non-string label, while **permitting** internal
  whitespace and non-ASCII.
- **Sector reaches the run.** `ExecutionPipeline` resolves the classification
  once per fill in `_apply_report_to_portfolio` and freezes it onto that fill's
  `TradeRecord.sector_id`. `pnl_by_sector` produces a real breakdown for the
  first time.
- **Exposure by sector.** `ExposureStatus.sector_exposure` is filled from the
  same authority, on **signed** market value so the buckets sum to
  `net_exposure` over the classified positions, inside the pass that already
  walked them.

## Changed

- `_risk_exposure` is one pass rather than three. It previously built
  `asset_exposure` with a dict comprehension and then ran two `sum()` generators
  over the result; it now accumulates all four figures and the sector buckets in
  a single traversal. Every existing figure is unchanged, exactly — a
  no-registry run's serialized payload is byte-identical to v2.10.0's.
- `_sync_risk_from_portfolio` and `_risk_exposure` take the registry rather than
  defaulting it, so no call site can silently stop reporting. Both are private.
- `_trade_record` takes `sector` as an argument and stays a pure formatter with
  no access to pipeline state.
- `ExecutionPipelineConfig.instruments` is documented as read for two things
  rather than one. `record_for` remains the only method ever called on it: the
  pipeline still never resolves a provider symbol, derives an `asset_id`,
  registers anything, or classifies anything.

## Fixed

- **A field ADR-0016 called mutable could not be changed.** Reclassifying an
  instrument raised `InstrumentRegistrationError`. `classify_instrument` is a
  separate function rather than a relaxation of that refusal: relaxing it would
  make the rule depend on *which* field differs and leave accidental content
  drift indistinguishable from a deliberate reclassification. Re-registering a
  stale, unclassified record over a classified one is still refused.
- Three documentation defects found during v2.11 archaeology, all pre-existing:
  `ExecutionPipelineState.unpriced_assets` said "Not persisted; nothing captures
  this state" while `alphalab.runtime.snapshot` has captured and restored it
  since v2.9; the v2.9 changelog gave `apply_terminal_outcome` a signature it
  never had; and `test_no_session_or_run_snapshot_exists_yet` was written
  mid-v2.9 and had been vacuously true since the end of that same release. The
  test keeps its assertions, which are still worth pinning, and is renamed for
  what it actually checks.

## Unchanged, deliberately

`alphalab.analytics.attribution` — not opened. The consumer was already correct:
it buckets by `sector_id` when present, omits the trade when it is `None`, and
refuses a placeholder bucket. Touching a working consumer to make a supplier's
change look larger is the unnecessary refactor this release refuses.

Instrument identity derivation, `canonical_instrument_key`,
`ALPHALAB_INSTRUMENT_NAMESPACE`, `INSTRUMENT_KEY_SCHEME`, alias storage and
resolution, `register_instrument`'s refusal, `InstrumentRecord`'s constructor,
`RiskLimits` and every risk check — no check reads a sector, because
`sector_exposure` is visibility and not enforcement.

**No schema constant moves.** `PIPELINE_SNAPSHOT_SCHEMA` stays 2,
`SESSION_`, `BACKTEST_`, `ALLOCATION_`, `OMS_` and `LIFECYCLE_SNAPSHOT_SCHEMA`
stay 1, `PORTFOLIO_SNAPSHOT_SCHEMA` stays 2. `TradeRecord.sector_id` and
`ExposureStatus.sector_exposure` are existing persisted fields with existing
decoders; their values change and their shape does not.

**No exception class is added.** `InstrumentRegistrationError` means a
registration would replace one identity with another, and classification
provably cannot: the signature cannot express it, and `dataclasses.replace`
refuses `asset_id` outright because that field is `init=False`.

**No live trading is added.** No broker transport, no streaming source, no venue
connectivity.

## Deferred, unchanged

Any taxonomy or classification dataset — AlphaLab ships no reference data, so a
breakdown requires an operator who declares one; classification dimensions other
than sector; a canonical or case-folded sector vocabulary; distinguishing *why*
a sector is unknown, in the manner of `UnpricedReason`; sector-based risk limits;
persisting the `InstrumentRegistry`, or checking on `restore` that a supplied
registry classifies as the captured run did; `InstrumentRecord.currency` reaching
`Position.currency`, and the FX rate source and multi-currency valuation that
depend on it; `StrategyContext.history` and `.universe`; broker transport,
streaming market data and reconnect; artifact storage; the approval workflow over
`alphalab.enterprise`'s RBAC and audit log; and the removal of `integrations`,
`kernel`, `core.events` or `CommonEvent`.

## Verification

2926 tests pass, up from 2818. Strict mypy is clean over 896 source files, Ruff
lint and format are clean, and the wheel and sdist build and validate.

**Identity stability is pinned three ways**, because that is the guarantee
ADR-0016 N5 exists to protect: `sector` is outside the key, the classification
signature exposes no identity field, and `dataclasses.replace(record,
asset_id=...)` raises. ADR-0016's golden identifier
`2b670078-27a6-57c2-b359-4e64d8809ea2` is re-derived from a *classified* record.

**A run with `instruments=None` is byte-identical to v2.10.0** — measured, not
asserted. The v2.10.0 tree was extracted and run beside the candidate on one
machine over a seeded four-fill workload: the serialized pipeline payload matches
byte for byte, the identifier stream reaches the same 97 draws, and every
exposure figure is equal.

**The performance gate for `sector_exposure` passed on every measure.**
Interleaved against v2.10.0, 14 samples per cell: 1k best −0.47%, 1k median
−0.28%, 4k best −0.74%, 4k median −0.68%, scaling 4.124 → 4.113. All inside the
3% tolerance and all in the faster direction, because the single-pass rewrite
removed two traversals the old implementation made. The classified path itself
costs nothing measurable against a registry-configured-but-unclassified control:
+0.07% at 1k, −0.21% at 4k.

Simulated and venue fills agree on the sector **structurally**: both reach
`_apply_report_to_portfolio`, which is the only place the registry is read for a
fill, and a test drives `apply_execution_report` to prove it. Backtest, replay
and paper agree on every sector.

Reclassification is `O(1)` and shares structure: 200 successive classifications
of one instrument leave the `PersistentMap` store object identical and the
registry the same size.

## Architecture decisions

**ADR-0027** settles instrument classification and the sector provenance
boundary: the classification API and why it is keyed by `asset_id`, the sector
label rule and why it is not `normalize_key_field`, reclassification semantics,
the two-tenses provenance model, the single fill-path integration point, and the
compatibility position. Written before the implementation; its status line now
records what shipped. The one addition made during implementation is decision 3's
residual paragraph, which states that the label rule binds the classification
path and not `InstrumentRecord`'s constructor.

**ADR-0016** gains a status amendment naming exactly which two of its statements
this supersedes — testing invariant 9's second clause, conditionally, and the
non-goal on sector values, for the mechanism only — and which one it relies on:
N5's exclusion of `sector` from the identity key, unchanged.

---

# [2.10.0] - 2026-09-07

**The Strategy Boundary.**

v2.9 shipped a durable-continuation guarantee with four preconditions. Three
were the caller's and could be met. The fourth — "strategy-internal state
restored by the caller" — could be met by nobody, because `StrategyProtocol`
declared ten hooks and no way to express it: a strategy holding a rolling
window, a counter or a fitted model held state that was captured by nothing,
restored by nothing and, worst of all, **detected by nothing**. Restore
succeeded, every Class-1 value compared equal, and the run diverged on the next
event with nothing raising. The opposite direction was as empty:
`StrategyContext` had existed since v0.10.0 with nine fields, and every
construction site in the repository passed `object()` for six of them, so a
strategy could observe the event handed to its hook and nothing else — not its
position, not its cash, not its own orders, not the price it was about to be
marked at. This release closes both.

## Added

- `alphalab.strategy.protocol.StrategyStateProtocol` -- a **second, separate**
  protocol a strategy satisfies to declare durable internal state, with three
  members: `strategy_state_version`, `capture_state` and `restore_state`.
  Separate so that `StrategyProtocol` is unchanged and every existing strategy
  keeps compiling and keeps its current, conditional guarantee. Declaration is
  structural and opt-in; nothing introspects attributes to guess at state, and
  `BaseStrategy` deliberately provides no defaults, because inheriting a no-op
  `capture_state` would make every strategy claim state it does not have.
- **A required two-sided codec.** Both directions or neither: a strategy
  defining an encode and no decode is refused at capture rather than read as
  declaring nothing. The reason is measurable rather than stylistic -- the
  shared encoder writes `Decimal("1.25")` and `"1.25"` identically, and a
  `tuple` as a `list`, so no generic decoder can recover the type afterwards.
  "Restrict strategy state to values the encoder supports" specifies the write
  direction, which already worked, and leaves the read direction unspecified,
  which is the one that was broken.
- `StrategyStateRecord`, `NOT_ASKED` and `READABLE_PIPELINE_SCHEMAS` in
  `alphalab.runtime.snapshot`, and a **three-valued** `StrategyRecord.state`:
  the field absent means a version-1 payload whose writer never asked, `null`
  means the strategy declared none, and an object means it declared. `{}` and
  `null` never collapse.
- `alphalab.runtime.context_views` -- `PortfolioView`, `OrderView`,
  `OrderShare`, `RiskView`, `MarketView` and `order_shares_by_strategy`. Every
  view is a reference to state the pipeline already holds; nothing is copied and
  no view owns a fact.
- **A strategy sees the marked portfolio.** `ExecutionPipeline` assembles the
  context from the marked-portfolio and resynced-risk locals that already
  existed two lines above the dispatch that could not see them, never from the
  pre-mark `state.portfolio` -- reading those would show a strategy a book
  marked at the previous event's prices while risk evaluated its order against
  these. Risk headroom and a market view ship alongside, both being reference
  assembly over state already on the pipeline.
- **Strategy-scoped live order shares.** A strategy sees its *share* of an
  order, never sole ownership: two strategies whose intents net into one
  `BUY 100` see 60 and 40, and `OrderShare.is_sole_contributor` is `False` for
  both. The view covers live orders only, because ADR-0021 retires a
  contribution when its order reaches a terminal state.

## Changed

- `PIPELINE_SNAPSHOT_SCHEMA` moves **1 -> 2**, for one field per strategy
  record. `SESSION_SNAPSHOT_SCHEMA`, `BACKTEST_SNAPSHOT_SCHEMA`,
  `ALLOCATION_SNAPSHOT_SCHEMA`, `OMS_SNAPSHOT_SCHEMA`,
  `PORTFOLIO_SNAPSHOT_SCHEMA` and `LIFECYCLE_SNAPSHOT_SCHEMA` do **not** move.
  That is ADR-0023's envelope split working as designed, exercised for the first
  time: the run envelopes nest the pipeline payload and its decoder validates its
  own version.
- **Version-1 pipeline payloads stay readable**, and restore every strategy as
  "not asked". That is the OMS precedent rather than the portfolio's: a v1
  payload is missing nothing, and "no strategy state was captured" is an
  accurate reading of it rather than an invented value. One bounded exception --
  a *declaring* instance against a v1 payload is refused, because starting it
  empty would invent the one fact that decides whether the continuation is
  correct.
- **Capture validates and normalizes strategy state at capture time.** The value
  goes through the existing `DeterministicEncoder` immediately, so an
  unencodable state can never reach a `PipelineSnapshot` that looks valid in
  memory and fails at an unrelated `serialize` later. The same step leaves the
  record carrying JSON-decoded primitives, so `restore_state` receives one shape
  whether the snapshot travelled through JSON or not -- without it the in-memory
  path handed back `Decimal` and `tuple` while the JSON path handed back `str`
  and `list`, and a codec written against one would break on the other.
- `StrategyEngine.process_event` builds a context **only for a running
  strategy**. It previously built one for every registered strategy and
  `Dispatcher` discarded it a moment later, which was free while a context held
  placeholders. Dispatch semantics are unchanged and `Dispatcher` keeps its own
  guard.
- `ExecutionPipeline.process_market_event` overlays the pipeline-owned context
  fields onto the caller's factory result. `ContextFactory` keeps its signature,
  every existing construction site keeps working, and `clock`, `logger` and
  `config` pass through as the caller's own objects -- which is what keeps
  `alphalab.reinforcement_learning`'s `_PendingDecision` channel working
  untouched. A pipeline-owned field always wins, so no caller can install a
  second source of truth.

## Fixed

- Four reconciliation mismatches now refuse the **whole** restore, naming the
  strategy, with the original exception chained: a declaring instance against a
  version-1 payload, a declaring instance against a recorded `null`, a
  non-declaring instance against a payload carrying state, and a strategy's own
  decode raising. Nothing partial is returned; restoring the other strategies and
  skipping one would produce a state that is internally consistent, compares
  equal on every Class-1 value, and is wrong.

## Unchanged, deliberately

`StrategyProtocol` (ten hooks, none added, none revived), `StrategyState`,
`StrategyContext`'s field names, arity and types, `ContextFactory`'s signature,
`DeterministicEncoder` (no branch was added for strategy state; a strategy type
with no JSON form uses `__serializable__`, which already existed),
`PersistenceProtocol`, `SessionState`, `BacktestState`, `PersistentMap`, and
`alphalab.strategy` as a strict leaf importing only `alphalab.common` and
itself.

`StrategyRecord.config` keeps its v2.9 semantics exactly -- still `Any`, still
`config=require(payload, "config")`. Its JSON round-trip lossiness is
pre-existing, is a property of the shared encoder and of JSON rather than of
anything decided here, and ADR-0025 decision 4 records that a separate decision
is required before it changes.

**No live trading is added.** No broker transport, no streaming source, no venue
connectivity. Nothing in this release reaches a market.

## Deferred, unchanged

`StrategyContext.history` and `.universe`; allocation visibility through the
context; mutable runtime services in any context field; reviving `on_fill`,
`on_order` or `on_timer`, which would require a second strategy dispatch per
event and change intent ordering and every parity baseline; unifying
`SessionState` and `BacktestState`; promoting the four cross-package private
decoders; broker transport, streaming market data and reconnect; artifact
storage; a security master and sector attribution; an FX rate source and true
multi-currency valuation; the approval workflow over `alphalab.enterprise`'s
RBAC and audit log; and the removal of `integrations`, `kernel`, `core.events`
or `CommonEvent`.

## Verification

2818 tests pass. Independent certification -- probes written outside the
repository rather than reruns of the shipped suite -- exercised a
counter/deque/`Decimal`/nested-mapping/`__serializable__` strategy through
capture, JSON, restore into a **fresh** instance and continuation at every
record boundary of single- and multi-strategy datasets, and found Class-1 state,
declared strategy state, identifier sequences and behaviour identical to the
uninterrupted control in every case. Capture, restore and context assembly draw
**zero** identifiers, dispatch no strategy and mutate no state. Cross-engine
parity was measured rather than inferred: `BacktestEngine.run` and
`TradingSession(mode=BACKTEST)` agree on Class-1 state, strategy state,
processed count and identifier position, backtest equals replay equals paper,
and live differs only in routing.

The negative case is retained rather than removed: a stateful strategy that
declines to declare state **still diverges** across a fresh restore, and a test
names that expectation. That is the documented behaviour of a non-declaring
strategy, not a defect.

Attribution is `O(1)` when the contribution ledger is empty -- flat across 100,
500 and 2,000 historical orders -- and linear in live contributions at roughly
2.3 microseconds each. Context assembly is `O(1)` with respect to portfolio and
order-book size, and strategy-state capture scales with the size of the declared
state rather than with run length.

Pipeline throughput is materially equivalent to v2.9.0. Measured back to back on
one machine, v2.9.0 itself runs at roughly 3,466-3,479 events/sec with
1k -> 4k scaling of about 4.58-4.64x under current conditions, and this release
measures within run-to-run noise of that. The `<= 4.5x` scaling figure quoted
during v2.10 planning was taken when the machine was in a faster state and is
**not** met by the v2.9.0 release either; it is recorded here as a threshold that
needs recalibrating rather than as a target this release achieved.

## Architecture decisions

Two ADRs are written to disk, both drafted before their implementation and both
now recording what shipped. **ADR-0025** settles strategy-state ownership and
the capture contract -- the two-sided codec, the per-strategy version
independent of `PIPELINE_SNAPSHOT_SCHEMA`, the version-1 compatibility rule, the
failure taxonomy, and the explicit exclusion of `StrategyRecord.config`.
**ADR-0026** settles `StrategyContext` population and the visibility boundary --
what is populated and what stays empty, contribution-based order attribution,
the read-only boundary, and the factory overlay. ADR-0026's *Data model changes*
records the one place the implementation departed from its first draft: the
supporting marker protocols stay intentionally minimal rather than gaining the
views' methods, because typed members would make `alphalab.strategy` depend on
four domain packages, and the concrete views live outside that package instead.

---

# [2.9.0] - 2026-09-07

**Durable Run State.**

A run could not stop and continue. Every *quantity* already survived a round
trip -- cash, realized P&L, commission, positions, reservations, contributions,
`notional_allocated`, risk NAV, open orders -- but nothing recorded where the
identifier stream had reached, so a run that stopped and resumed re-entered
`id_scope`, built a fresh source positioned at zero, and drew identifiers it had
already used. Nothing raised at any layer. Around that defect sat four more:
nothing captured the composite state the execution path threads, the OMS payload
declared no schema version, a venue fill delivered twice was applied twice, an
externally routed order a venue had already ended could not be ended here, and
appending to `alphalab.persistence` was quadratic. This release closes all of
them.

## Added

- `alphalab.runtime.snapshot` -- `capture` / `restore` / `from_primitives` for
  `ExecutionPipelineState` under `PIPELINE_SNAPSHOT_SCHEMA = 1`. This is the
  state every environment threads: a backtest, a replay, a paper run and a live
  session all advance the same value through the same step, and until now
  nothing captured it. Eleven of its fifteen fields already serialized as they
  stood; the obstacle was never the state model but that four values are not
  data and no contract said what to do about them. The envelope nests the
  portfolio, OMS and allocation snapshots rather than decoding their contents a
  second time, and carries market, risk, execution, analytics and the strategy
  runtime inline under its own version.
- `alphalab.runtime.session_snapshot` (`SESSION_SNAPSHOT_SCHEMA = 1`) and
  `alphalab.backtesting.snapshot` (`BACKTEST_SNAPSHOT_SCHEMA = 1`) -- the run
  bookkeeping around that core, one envelope per owning package. Two rather than
  one because the dependency runs one way: `alphalab.backtesting` imports
  `alphalab.runtime` and never the reverse, so a single shared module would close
  an import cycle. Each nests the pipeline snapshot, and each carries the
  pipeline configuration exactly once, because `state.config.pipeline` and
  `state.pipeline.config` are the same object and two copies could disagree.
- `alphalab.allocation.snapshot` -- `ALLOCATION_SNAPSHOT_SCHEMA = 1` for the two
  ledgers that decide whether a restored run's committed capital and its
  attribution are correct. First-class rather than inline because ADR-0021 made
  their lifetime exact and ADR-0024 gave a restored working order a way to end,
  and neither guarantee survives a round trip the ledgers do not.
- `TradingSession.resume(state)` and `BacktestEngine.resume(state)` -- context
  managers that open the identifier scope a restored stream position implies.
  `restore` reconstructs state and does nothing else; `resume` is where
  continuation begins. The boundary is *between* `advance` calls: a run that
  processed N records resumes at record N+1 and replays nothing.
- `alphalab.common.ids.IdStreamPosition` -- `(seed, draws)`, the fact the repository
  never held. `DeterministicIdSource` counts what it mints, `current_id_position`
  reads the ambient cursor, `ExecutionPipelineState.id_position` stores it at each
  step boundary, and `id_source_for` rebuilds a source that has reached it. The
  position is two integers rather than a serialized generator state: it is
  readable, cross-checkable against the state it accompanies, and pins no
  generator implementation into the persisted format. Restore is therefore
  O(draws) -- roughly a microsecond per replayed draw, so a run that minted a
  million identifiers resumes in about a second, paid once.
- `ExecutionPipeline.apply_terminal_outcome(state, order, outcome, timestamp, reason="")`
  -- the route home for an externally routed working order the venue has ended
  as `CANCELLED`, `REJECTED` or `EXPIRED`. The caller supplies the outcome
  because the venue is the authority for what happened and the pipeline never
  infers it. `OMSEngine` moves the order and emits its event and
  `_release_if_terminal` retires both ledgers, exactly as they do for every other
  terminal transition. **No venue is contacted, no `BrokerState` is built, and
  `broker.reconciliation` is not consulted.**
- `OMS_SNAPSHOT_SCHEMA = 1` and `LEGACY_UNVERSIONED_V0_KEYS`
  (`alphalab.oms.snapshot`). `OMSSnapshot` was the last round-trip snapshot
  without a version field.
- `RuntimeObjects`, `SessionObjects` and `BacktestObjects` -- the live objects a
  snapshot records by type and requires the caller to supply back: the sizing
  model, the execution simulator, the optional instrument registry, each strategy
  instance, and the fill policy.

## Fixed

- **A continued run re-minted identifiers it had already used.** A seed says
  where a stream starts; nothing said where it had reached. Measured on v2.8.0,
  sweeping every restore point across a workload producing 41 identifiers found
  up to **4 duplicates** -- a later `fill_id` landing on a UUID an earlier
  `execution_id` already held -- against **zero** for the uninterrupted control
  over the same workload, so the restart caused them and not the workload.
  Nothing raised: two distinct facts in one run came to share an identifier and
  every layer accepted it. The same sweep on v2.9 over a 16-record seeded
  workload split at all 15 boundaries produces 257 identifiers and no duplicate,
  identical position for position to a run that never stopped.
- **A venue fill delivered twice was applied twice.**
  `ExecutionPipeline.apply_execution_report` is the seam a real venue arrives
  through, and `_apply_reports` never wrote to `ExecutionState`. The simulated
  path recorded a report because `ExecutionEngine.simulate` runs first; the venue
  path recorded nothing, so the `reports` map -- keyed by execution id, which is
  exactly the ledger a duplicate check reads -- stayed empty on the one path
  where redelivery happens. Measured on v2.8.0 for a partial fill: cash
  999,600 -> 999,200, position 4 -> 8, `filled_quantity` 4 -> 8, two pipeline
  fills for one venue execution. A *full* fill was caught only incidentally, by
  the OMS refusing to fill an order already `FILLED`. A repeated `execution_id`
  is now a no-op returning the state unchanged -- the rule
  `alphalab.broker.reconciliation` already stated for `DUPLICATE`, "a no-op
  rather than an error because a reconnect makes it routine", enforced over the
  pipeline's own ledger without importing the broker.
- **A working external order could not be ended.** Under `EXTERNAL` routing an
  accepted order is left working for a broker adapter and its reservation and
  contribution stay held, correctly. A venue fill had a route home through
  `apply_execution_report`; a rejection, cancellation or expiry had none, so the
  pipeline's whole public surface was seven methods and not one of them
  terminated an order. Measured on v2.8.0, six externally routed events left six
  open orders holding six reservations, six contributions and 3,000 of committed
  notional with no way to retire any of it. ADR-0021 recorded this as a known
  boundary; it is now closed.
- **Appending to `alphalab.persistence` was quadratic.**
  `MemoryStorage.append_event` rebuilt three containers per call -- the stored-event
  tuple, the `frozenset` of event ids and the system-event tuple -- and
  `save_snapshot` rebuilt the snapshot dict per save. Measured on v2.8.0, 32,000
  appends took **14.0 s** and each doubling cost ~4.5x the previous, so
  `benchmarks/benchmark_persistence.py`'s 100,000-event workload did not finish.
  The four fields now use `AppendOnlyLog`, `PersistentMap` and `PersistentSet` --
  the same containers v2.1 gave the engine histories and v2.2 gave the OMS order
  book, applied to the one package that missed them. The same 32,000 appends take
  **0.24 s**, the worst doubling is **2.02x**, and the 100,000-event benchmark
  completes in 1.97 s. `PersistenceProtocol` and every method signature are
  unchanged.
- **A restored state could hold a configuration `initialize` would have
  refused.** `_require_one_account_currency` -- v2.8's guarantee that
  `ExecutionPipelineConfig.currency` and `Account.base_currency` agree -- is
  called at exactly one site, inside `ExecutionPipeline.initialize`, and restore
  does not go through `initialize`. `restore` now re-runs every construction-time
  validation `initialize` enforces before returning a state, raising the same
  error with the same message. The rule is general, so a validation added to
  `initialize` later is covered.

## Changed

- **`OMSState` payloads now carry `schema_version`.** A stored payload of any
  vintage previously decoded as current, so the first schema change would have
  been a silent misread rather than a decision. `capture` never emits an
  unversioned payload. An unversioned payload is read **only** when its top-level
  key set is exactly `{orders, active_orders, completed_orders, history,
  events}`: the legacy shape with a key missing is refused, with an extra key is
  refused, any other unversioned shape is refused, and `schema_version >= 2` is
  refused. **A missing `schema_version` is never read as version 1.** It is sent
  to a total structural match a payload either passes whole or fails.
  Compatibility rather than a break because, unlike the portfolio's refused
  version 1, a pre-v2.9 OMS payload is missing no data at all -- every field the
  decoder reads is present -- and `alphalab.oms`'s module docstring teaches the
  JSON round trip as a public recipe, so those payloads exist by invitation.
- The regression guard on whole-state OMS serialization asserts a six-key
  payload where it asserted five. It is the same exact-set equality, not a
  loosened assertion.
- Execution-path throughput is unchanged within measurement noise: -0.38% at
  1,000 events and -1.15% at 4,000, against a 3% release tolerance. The OMS,
  replay and portfolio benchmarks are flat.

## Unchanged, deliberately

No breaking changes. `PersistenceProtocol`, `PORTFOLIO_SNAPSHOT_SCHEMA` (2) and
`LIFECYCLE_SNAPSHOT_SCHEMA` (1) do not move, and there is no migration
framework. `new_id`, `use_id_source`, `id_scope`, `derive_asset_id`, the frozen
instrument namespace and **every `new_id()` call site** are untouched: the
`ContextVar` was always a delivery mechanism and the defect was a missing state
field. `BrokerState` and `ExternalOrderMap` are not persisted -- a belief about a
venue is not durable state, and reconstructing one from a snapshot would assert
something AlphaLab cannot verify.

**This release does not add live trading.** No broker transport, credential or
venue call is introduced; `apply_terminal_outcome` is told what happened and
never asks.

**The equivalence contract is conditional and says so.** For a *seeded* run,
given the same records in the same order, the same supplied runtime objects, and
strategy-internal state restored by the caller, capture -> serialize ->
deserialize -> restore -> continue equals uninterrupted execution across
ADR-0023's Class-1 state and every deterministic identifier. `StrategyProtocol`
declares ten hooks and **no state-serialization hook**, so a strategy holding a
rolling window in Python attributes holds state AlphaLab cannot capture; the
fourth precondition is the caller's and a test names that expectation rather
than leaving it implicit. An **unseeded** run restores and continues with
quantities guaranteed and identifiers regenerated: `uuid4` cannot match a prior
run and cannot collide, and this carve-out is explicit rather than implied.

## Deferred, unchanged

`StrategyStateProtocol` and `StrategyContext` completion; unifying `SessionState`
and `BacktestState` or removing the parallel runtime mechanism; promoting the
four cross-package private decoders (`_order`, `_report`, `_fill`,
`_require_object`) to a public or shared surface; schema constants for market,
risk, execution, analytics or strategy state; a migration framework or
version-translation layer; broker transport, streaming market data and reconnect;
artifact storage; a security master and sector attribution; an FX rate source and
true multi-currency valuation; the approval workflow over `alphalab.enterprise`'s
RBAC and audit log; unifying `OMSState` and `BrokerState` authority; and the
removal of `integrations`, `kernel`, `core.events` or `CommonEvent`.

## Architecture decisions

Six ADRs are written to disk. **ADR-0019**, **ADR-0020** and **ADR-0021** record
decisions taken and shipped during v2.8 that were never written down; their
substance is not reopened. **ADR-0022** (deterministic identifier continuation),
**ADR-0023** (the run-state snapshot envelope) and **ADR-0024** (external order
recovery without a venue) are this release's decisions, and their status lines
now record the shipped implementation. ADR-0023's decision 1 records that the
run layer shipped as two envelopes rather than the single `RunSnapshot` it first
sketched, and why.

---

# [2.8.0] - 2026-09-06

**Currency Roles and Run Outcomes.**

Three fields stood for several things at once, and nothing noticed when they
disagreed. One configuration named two account currencies and silently switched
off two risk limits. One valuation added two currencies together and labelled
the result with one of them. One request could end without a fill and leave no
reason behind, and its allocation ledger entry behind forever.

## Added

- `ExecutionPipelineState.unpriced_assets` — the assets a run declined to trade
  for want of a price, keyed by `asset_id` so the size follows the distinct
  instruments a run's strategies named rather than the event count. Surfaced as
  read-only `unpriced_assets` properties on `BacktestResult`, `ReplayResult` and
  `SessionState`, which read through to the pipeline state so none can disagree
  with the run it describes. Not persisted.
- `UnpricedAsset` and `UnpricedReason` (`alphalab.runtime`, re-exported from
  `alphalab.backtesting`). `UnpricedAsset` keeps the reason, a sentence of
  detail, the first and last market timestamp and an occurrence count.
  `UnpricedReason` has exactly three members and no fourth for "unknown".
- `ExecutionPipelineConfig.instruments: InstrumentRegistry | None` — optional,
  read-only, and used for one thing: deciding whether a dropped request's
  `asset_id` names a registered instrument. Only `record_for` is ever called.
  The pipeline never resolves a provider symbol, never derives an `asset_id`
  and never registers anything; ADR-0016 leaves resolution at the wire boundary
  and this takes none of it back. `None` remains the default.
- `MixedCurrencyValuationError` (`alphalab.portfolio`), and
  `PortfolioValuation.assert_single_currency`.

## Fixed

- **A configuration could name two account currencies and say nothing.**
  `ExecutionPipelineConfig.currency` funds the cash ledger and denominates every
  `OrderInstruction`, `ExecutionReport` and `Position`; `Account.base_currency`
  is what the risk resync and `NAVCalculator` read. Nothing checked that they
  agreed. When they did not, cash landed under one and risk read the other, so
  `risk.cash`, `buying_power`, `current_nav` and `peak_nav` were all zero:
  `check_buying_power` refused every order, `check_leverage` returned early on a
  non-positive NAV, and `check_margin` passed vacuously. Measured with
  `currency="EUR"` against `base_currency="USD"` over six quotes — zero fills,
  six risk rejections, full starting equity reported, and two risk limits
  quietly not checking. Refused at the first statement of
  `ExecutionPipeline.initialize`, before the portfolio exists, so nothing is
  funded against a configuration that is about to be refused.
- **A valuation could span two currencies and report one number.**
  `PortfolioValuation.snapshot` read cash for the base currency alone, dropping
  every other balance, while `long_value` and `short_value` summed every
  position regardless of what it traded in. A book of 1000 USD and 500 EUR cash
  against 1100 USD and 1100 EUR of positions returned `equity=3200.00` labelled
  `"USD"` — a figure in no currency at all. Refused instead, on two conditions
  because there were two independent errors: every position must declare the
  base currency, and no other currency may hold a non-zero balance.
- **An allocation contribution could outlive its request.**
  `AllocationState.contributions` is retired by `_release_if_terminal`, which
  was reached only from inside the per-report loop — so any request or order
  ending without a report kept its entry forever. Four paths did: a request
  dropped for want of a price and one refused by risk never reach the OMS at
  all, while `NO_FILL` / `REJECTED` / `EXPIRED` and a withdrawn partial-fill
  remainder reach a terminal OMS state without producing a report. Twenty events
  on each path left twenty entries apiece, with no bound and no reader.
  Contributions are now retired wherever a request's lifecycle ends, exactly
  once, using the mechanism that already existed. Reservation release is
  unchanged on every path, and post-trade attribution still reads contributions
  at fill time.
- **A run could produce no fills and not say why.** Of the ways a request can
  end without a fill, every one but a dropped request left a reason: allocation
  and risk record their rejections, an externally routed order stays open in the
  OMS, and a non-trading execution closes its order with the status that ended
  it. A dropped request emitted only an `AllocationReservationReleased`, which
  is the identical event three other outcomes emit, and the reason lived on the
  per-event result and was gone when the run finished. This is the ADR-0016
  section 3 failure mode — a strategy naming an instrument the run never priced
  — which ADR-0016 documented and deferred.

## Changed

- `LIFECYCLE_SNAPSHOT_SCHEMA` is the literal `1` rather than an alias of
  `DEFAULT_SCHEMA_VERSION`, which is also the version of `CommonEvent` and
  `BaseEvent`. **The value does not move**, so no payload reads or writes
  differently; the lifecycle version is simply now independently settable. This
  is the trap v2.6 removed from `PortfolioSnapshot` and left standing here, and
  the prerequisite ADR-0018 names for the bump the governance release needs.

## Unchanged, deliberately

No breaking changes. No persisted type gains or loses a field, no schema version
moves, and there is no migration. `evidence_id_for` is byte-identical, so
evidence recorded under v2.6 and v2.7 still verifies and still passes its
policy. `canonical_instrument_key`, the frozen instrument namespace and
`asset_id` derivation are untouched, as are `Fill` / `Trade` validation,
`ExecutionPipelineResult.unpriced_requests`, `BacktestStep`, the OMS snapshot
format and every canonical market and execution model. No new dependency.

`PortfolioValuation.portfolio_value`, `long_value`, `short_value` and
`NAVCalculator.calculate` share the currency-blindness and are left exactly as
they were, with their present behaviour pinned by a test. `NAVCalculator` runs
on every market event through the risk resync, so guarding it is a hot-path
decision that belongs with the release supplying an FX rate source.

**This release does not add FX.** Refusing to aggregate two currencies is the
absence of a rate, not a rule that foreign-currency instruments are invalid: the
cash ledger is already keyed by currency and every position already declares its
own, so a wholly-EUR book values in EUR exactly as it did.

## Deferred, unchanged

FX and true multi-currency valuation; instrument currency reaching `Position` /
`CashLedger`; `Bar.currency`; `OMSSnapshot.schema_version` and the OMS
history/events envelope; `AllocationState` persistence and session round-trip;
governance actors and enterprise RBAC; richer persisted evidence provenance;
`ResearchState` dataset identity; `StrategyContext` population; live transport,
streaming and reconnect; sector classification; a dataset registry; an artifact
store; and the removal of `integrations`, `kernel`, `core.events` or
`CommonEvent`.

The design decisions behind this release -- that listing exchange, market-data
attribution and execution venue are three concepts and not one; that
`ExecutionPipelineConfig.currency` was standing for account, trade and
settlement currency at once; and that an unpriced request is a recorded outcome
rather than an error -- are not yet written up as ADRs.

---

# [2.7.0] - 2026-09-06

**Instrument Identity and Dataset Provenance.**

Two things that were asserted are now derived. A provider symbol becomes a
canonical instrument identity through an authority instead of being passed
through verbatim, and a run's evidence names the data the run actually consumed
instead of the data a caller claimed. Both were defects the repository
documented about itself.

## Added

- `alphalab.instrument` — the authority for what a provider symbol means.
  `InstrumentRegistry` owns `(provider, symbol) -> asset_id` and
  `asset_id -> InstrumentRecord`. A canonical `asset_id` is *derived*, not
  minted: `uuid5` over a fixed canonical key, under the frozen namespace
  `1935bdfa-e8c0-5611-ae10-607c3a67c19b`. Two independently configured
  environments therefore agree on the identity of one instrument with no shared
  database. Registration is still required — derivation alone would turn every
  typo into a new instrument. Registering the same record twice is a no-op;
  registering different content under an identifier the registry already holds,
  or pointing one provider symbol at a second instrument, is refused.
- `NormalizationPolicy.identity` — an explicit two-mode identity resolution,
  either an `InstrumentRegistry` or a named `UnresolvedIdentity`. It is never
  `None`: an absent value silently selecting the unsafe behaviour is the shape
  of the defect this release removes.
- `InstrumentResolutionError` (`alphalab.market.exceptions`) — raised at the
  wire boundary, naming the provider and the symbol.
- `BacktestResult.dataset_id` and `SessionState.source_id` — a finished run
  names the data it consumed. Both default to `None`, which is an honest
  absence rather than an invented identity.
- `ReplayResult.dataset_id` — reads through to the run it wraps, so a replay
  cannot disagree with its own backtest.

## Fixed

- **The documented provider path could not reach a fill (F-1).**
  `market.normalization` turned a provider symbol into an `asset_id` verbatim;
  every stage from `Quote` to `ExecutionReport` carried `asset_id` as an
  unconstrained `str`; and `core.Fill` refused anything that was not a UUID. The
  identity was wrong from the first record and nothing objected until the last,
  so market data, the strategy, allocation and risk all succeeded and the run
  died at the execution -> core adapter naming neither the provider nor the
  symbol. The only configuration that reached a fill was one where an operator
  hand-authored a UUID per instrument. Refusal now happens where the identity is
  created, and a registered instrument reaches a fill through the real path.
- **Evidence claimed a dataset instead of recording one.**
  `evidence_from_backtest` took a `dataset_id` argument, and `evidence_id_for`
  hashes that value — so the digest was only as trustworthy as a string somebody
  typed, and two runs over genuinely different data could be handed one identity,
  verify cleanly and pass the gate. `BACKTEST` evidence now derives the dataset
  from the run, and a run that names none is refused rather than recorded with a
  fabricated `""`.

## Changed — breaking

- **B1.** `NormalizationPolicy.symbols` is removed. A `SymbolMap` now lives on
  the identity mode: `NormalizationPolicy(symbols=SymbolMap({...}))` becomes
  `NormalizationPolicy(identity=UnresolvedIdentity(SymbolMap({...})))`. Two
  fields answering "what instrument is this symbol?" would be two sources of
  truth for one question.
- **B2.** `ProviderHistorySource.of` no longer defaults its `policy` argument.
  A defaulted parameter whose default value is always refused is a trap.
- **B3.** `evidence_from_backtest(result, subject, produced_at)` — the
  `dataset_id` parameter is removed, not accepted and ignored. An argument that
  is silently discarded leaves a caller believing they set something.
- **B4.** `DEFAULT_POLICY` is not a production execution configuration. Its
  identity mode is `UnresolvedIdentity`, which yields provider symbols, and
  `ProviderHistorySource.of` refuses it before calling the provider.

## Unchanged, deliberately

- `core.Fill` and `core.Trade` UUID validation. This release supplies a producer
  that can satisfy the existing invariant; it does not relax it.
- `evidence_id_for`, `ValidationEvidence`, `build_evidence`,
  `verify_evidence_id` and `evidence_from_research` are byte-identical to
  v2.6.0. Because the digest did not move, evidence recorded under v2.6 still
  verifies and still passes the policy it was promoted under — so this release
  needs no schema bump, no migration and no dual verification.
- `LIFECYCLE_SNAPSHOT_SCHEMA` remains 1 and `PORTFOLIO_SNAPSHOT_SCHEMA` remains
  2. No persisted type gained a field.
- `Intent` is unchanged and unvalidated, and `alphalab.strategy` acquires no
  dependency on `alphalab.instrument` or `alphalab.market`. The consistency
  guarantee is conditional on a strategy resolving through the canonical API;
  nothing intercepts an `Intent` that does not.

## Not in this release

Carried forward and explicitly **not** delivered: sector classification and a
security-master classification source (`InstrumentRecord.sector` is declared and
left `None`, and `pnl_by_sector` is still empty on the execution path);
governance actors and enterprise RBAC enforcement (ADR-0018 is written and
deferred); richer persisted provenance on evidence — source, time coverage,
normalization policy, provider identity; a dataset registry or uniqueness
guarantee; an artifact or object store; per-environment promotion policy;
code/version identity on runs; multi-currency valuation; live venue transport;
and the removal of `alphalab.integrations`, `alphalab.kernel` or
`alphalab.core.events`.

## Architecture decisions

- **ADR-0016** — Instrument Identity and Resolution Authority (Accepted).
- **ADR-0017** — Dataset Identity and Evidence Derivation (Accepted).
- **ADR-0018** — Governance Actors Across the Lifecycle / Enterprise Boundary
  (Proposed, deferred from v2.7).

---

# [2.6.0] - 2026-09-06

## Overview

AlphaLab 2.6.0 is "Allocation Authority and Attribution Truth". It is a
correctness release: no new engine packages, one new module
(`alphalab.core.contribution`), and two production-path numbers that were
plausible and wrong are now true.

Capital committed to orders that have not settled did not count against the
budget. Under `SIMULATED` routing that was invisible, because a reservation is
consumed or released inside the event that created it. Under `EXTERNAL` routing
— the routing ADR-0012 introduced for live — an accepted order stays working
and keeps its reservation by design, and six market events committed **5,400,000
against a 1,000,000 budget** with no position held and not one rejection
recorded.

Strategy identity was destroyed one statement before netting, and every emitted
order was stamped with the constant `"ALLOC-NETTED"` — including an order from a
single intent with no netting at all. Every strategy-level number the repository
could produce was a statement about a strategy that does not exist.

A third defect was found while verifying the first and is fixed with it: a
reservation is denominated at the reference price while consumption is
denominated at the execution price, so any fill priced away from the reference
stranded a residual on a terminal order, without bound.

See `docs/ADR/0015-allocation-authority-and-attribution-truth.md`.

## Added

- **`alphalab.core.contribution`** — `StrategyContribution(strategy_id,
  quantity)`, carrying a strategy's signed pre-netting share of a netted order,
  plus `contributions_from` for canonical aggregation. In `core` rather than
  `allocation` because it is a field of `OrderRequest` (ADR-0008); re-exported
  from `alphalab.allocation`.
- **`OrderRequest.contributions`** and **`TradeRecord.contributions`**, ordered
  by `strategy_id` so intent arrival order cannot change a request's value.
- **`AllocationState.contributions`** — a per-order ledger parallel to
  `reservations`, retired at the terminal OMS transition.
- **`AllocationEngine.contributions_for` / `retire_contributions`**.
- **`analytics.split_realized_pnl`** — signed weight `q_i / net_q`, with the
  last share obtained by subtraction so the parts sum exactly.
- **`Position.opened_at`** — when the current exposure began.
- Deprecation notices for `alphalab.integrations`, `alphalab.kernel` and
  `alphalab.common.CommonEvent`, all removed in v3.0.

## Fixed

- **Outstanding capital is enforced.** `AllocationEngine.allocate` compares
  `notional_allocated + total_notional` against both `available_global_capital`
  and `maximum_exposure`. Rejection stays whole-batch and atomic: `history`,
  `reservations` and `notional_allocated` are byte-identical after a refusal.
- **A terminal order releases whatever it still holds.** Released at the
  terminal OMS transition through the existing membership-guarded helper, so it
  is idempotent against every release point that already existed and covers both
  routings — including a venue fill arriving through `apply_execution_report`
  with no slippage model configured. Thirty round trips at a 1% adverse price
  previously stranded 3,000 against a 1,000,000 budget in a run ending flat.
- **`avg_holding_period` was a mean of zeros.** Holding periods are now measured
  from `Position.opened_at` for fills that reduced or closed a position.

## Changed — breaking

- **`TradeRecord` shape.** `strategy_id: str` is replaced by `contributions`;
  `sector_id` becomes `str | None`; `holding_period_seconds` becomes
  `float | None`. Positional construction breaks. No alias is provided: an alias
  would keep returning the wrong answer under a familiar name.
- **`PORTFOLIO_SNAPSHOT_SCHEMA` is 2, and version 1 payloads are refused.** No
  migration framework — a v1 payload does not record when a position opened, and
  `last_updated` would report a holding period of roughly zero for a position
  held a year. `DEFAULT_SCHEMA_VERSION`, `LIFECYCLE_SNAPSHOT_SCHEMA`,
  `CommonEvent.schema_version` and `BaseEvent.schema_version` remain **1**; the
  portfolio constant no longer aliases the shared one.
- **`OrderRequest.strategy_id` is `""` for every allocation-produced request.**
  `"ALLOC-NETTED"` is deleted. `oms.Order.strategy_id` and
  `ExecutionReport.strategy_id` carry the same empty value, and an order
  declaring no strategy is not entered in the OMS strategy index — so
  `orders_for_strategy` no longer answers for a fiction. The index stays
  single-valued and the query is neither renamed nor deprecated: it remains
  correct for callers who supply a real strategy id.
- **`pnl_by_sector` is empty for pipeline-produced reports.** There is no
  security master; a caller who has sector data still gets a breakdown.
- **`EXTERNAL` routing can now refuse an allocation** that would over-commit.
  Backtest, replay and paper are behaviourally unchanged.
- `IntentAllocator.size_intents` returns `(strategy_id, instrument, quantity)`
  triples; `NettingEngine.net_quantities` takes them.

## Deprecated

| Surface | Mechanism | Removed |
| --- | --- | --- |
| `alphalab.integrations` | `DeprecationWarning` at import | v3.0 |
| `alphalab.kernel` | `DeprecationWarning` at import | v3.0 |
| `alphalab.common.CommonEvent` | `DeprecationWarning` on use (PEP 562) | v3.0 |
| `alphalab.core.events` | documentation and ADR only — see below | v3.0 |
| `alphalab.allocation.BudgetExceededError` | documented as never raised | v3.0 |

`alphalab.core.events` deliberately emits **no** runtime warning.
`alphalab.core` re-exports thirteen of its symbols eagerly, so an import-time
warning there would fire on the canonical core package — which the whole
execution path depends on — for every consumer on every run.

## Documentation

Seven verified contradictions corrected (D-1…D-7): ADR-0014's claim that every
snapshot envelope carries `schema_version` (`OMSSnapshot` does not);
`ARCHITECTURE.md`'s stale partial-fill limitation, which the same document
contradicted; the allocation ledger table; `total_notional_allocated`'s
"historically" (it is outstanding, and falls); `OrderRequest.strategy_id`'s
claim that the sentinel applied only to cross-strategy netting; `TradeRecord`'s
"round-trip trade" (one record is produced per fill); and `BudgetExceededError`,
exported and raised nowhere.

## Not in scope

Unchanged and explicitly excluded: real broker transport, streaming market data,
an async live runtime, reconnect scheduling, order-state polling, multi-currency
valuation, artifact storage, Enterprise RBAC enforcement, dataset provenance, a
security master, a universal runtime, lifecycle → execution integration, and the
removal of `integrations`, `kernel`, `core.events` or `CommonEvent`. The
duplicate `ExecutionReceived` in `alphalab.broker` and `alphalab.brokers` is
**not** collapsed: they declare the same four fields in opposite order and are
both constructed positionally, so collapsing them would silently swap price and
quantity at one call site. Recorded in ADR-0015 for v3.0.

## Quality

| Gate | Result |
| --- | --- |
| `pytest -q` | **2122 passed** (2008 at v2.5.0) — 1698 unit, 109 integration, 315 regression |
| `mypy .` (strict) | clean, 924 source files |
| `ruff check .` / `ruff format --check .` | clean |

---

# [2.5.0] - 2026-09-05

## Overview

AlphaLab 2.5.0 is "State Round-Trip and the Live Data Path". It takes three
capabilities that already existed, were already tested, and were unreachable,
and makes them reachable — plus it decides two behaviours that had never been
decided.

Every AlphaLab state has serialized deterministically since v2.1. Exactly one
could be read back. v2.3 built a market-data normalization boundary and an
adapter protocol, and nothing in the repository joined them. The replay cursor
carried an O(N²) on a path v2.2 had wired into execution, and the benchmark
that nominally covered replay was written to avoid measuring it.

No new engine packages. Two new modules (`alphalab.market.provider`,
`alphalab.persistence.decode`), two new snapshot modules
(`alphalab.portfolio.snapshot`, `alphalab.lifecycle.snapshot`).

See `docs/ADR/0014-state-round-trip-and-the-live-data-path.md`.

---

## Added

### Typed state round-trip — `capture` / `restore`

- `alphalab.portfolio.snapshot` and `alphalab.lifecycle.snapshot` join
  `alphalab.oms.snapshot`: `capture(state)` → serializable projection,
  `from_primitives(payload)` → typed snapshot, `restore(snapshot)` → typed state.
- The contract, applied identically to every state: `restore(capture(s)) == s`.
  The restored value **compares equal**; it does not reproduce internal container
  lineage, and nothing can observe the difference. That is the position the
  repository already held — `oms.snapshot.restore` rebuilds the order book's
  indices by replaying `add`, and its test asserts equality against a state whose
  lineage is entirely different.
- Every snapshot carries `schema_version` and refuses one it does not read.
  There is no migration path in v2.5 because there is nothing to migrate from;
  the field exists so the first schema change is a decision rather than a silent
  misread.

### Typed decoding — `alphalab.persistence.decode`

- `require`, `as_decimal`, `as_optional_decimal`, `as_float`, `as_int`,
  `as_bool`, `as_str`, `as_optional_str`, `as_mapping`, `as_sequence`,
  `as_str_mapping`, `as_decimal_mapping`, `as_named_enum`, `as_value_enum`,
  `require_schema_version`. Each raises the new `StateDecodeError` **naming the
  field**.
- Deliberately *not* a reflective object mapper. A domain package states field by
  field what it expects, because that is where a wrong type or a missing key is
  caught. The failure this repository already had once — v2.1's append-only logs
  persisted as `"AppendOnlyLog([...])"` — came from a layer that accepted
  anything.
- Two enum encodings, two decoders, no guessing: a `StrEnum` is written as its
  value, a plain `Enum` through `str()` as `"Cls.NAME"`. A bare `"STAGING"` is
  refused, because accepting a form the encoder never writes is how a format
  acquires two dialects.
- `Decimal(str(value))`, the same conversion `market.normalization` uses, so a
  price between cents comes back as the number that was written.

### `PersistenceAdapter.snapshot_payload`

- The read direction, so a stored `Snapshot` can reach a domain decoder. It stops
  at primitives: the dependency runs domain → persistence and not back, or the
  package would have to import half of AlphaLab to read anything.
- `alphalab.persistence` now has production consumers for the first time. At
  v2.4 nothing in `alphalab/` imported it.

### The live data path — `alphalab.market.provider`

- `ProviderHistorySource` turns a provider adapter's historical bars into
  canonical `MarketRecord`s through `market.normalization`, and satisfies
  `MarketDataSource`. This is the link v2.3 was missing: before it,
  `normalize_wire_*` had no production caller and `SequenceSource` was the only
  source in the repository.
- `BarHistoryProvider` is deliberately narrower than any provider adapter's
  surface — `request_history` is the whole contract — so a test double can be a
  source without implementing connect, subscribe, quotes and books too.
- `normalize_wire_bars` maps a sequence; every normalization rule stays where
  v2.3 put it.
- A **history** source: finite, closed range, re-iterable, deterministic record
  ids on the `"<source_id>-<index>"` scheme `SequenceSource` and `MarketDataset`
  already use. No polling, no subscription, no reconnect, no streaming.
- Several symbols are interleaved by timestamp with an `asset_id` tie-break — a
  merge of already-sorted inputs, not a reordering. `validate_ordering` runs over
  the result either way.

### Ordering semantics — `SessionConfig.ordering`

- `CHRONOLOGICAL` (default): a record whose timestamp regresses **raises**. The
  source broke the guarantee it declared.
- `UNORDERED`: such a record is **skipped and recorded** on
  `SessionState.skipped`, the machinery the staleness gate already established,
  so an `UNORDERED` source cannot silently produce a run that merely looks
  chronological.
- A source declaring `UNORDERED` handed to a `CHRONOLOGICAL` session is refused
  by `TradingSession.run` before any record is processed — a session that would
  abort partway should not start.
- Nothing is buffered, reordered or held back. `MarketEngine.publish_quote`
  writes `latest_quotes` unconditionally and the pipeline marks the portfolio to
  whatever it finds, so a late record rewrites valuation backwards; AlphaLab says
  so rather than guessing.

### Benchmarks

- `benchmarks/benchmark_replay_engine.py` now measures `step_one_event` — the API
  the integrated replay path uses — across three sizes, and keeps the batch drain
  as a second figure rather than the only one.

---

## Changed

### A partially filled simulated order withdraws its remainder

The pipeline has always stated its rule in `_close_unfilled_order`: it mints a
fresh order per market event and never re-works an existing one, so an unfilled
order is withdrawn rather than left open. Every non-trading outcome was withdrawn
under that rule. A partial fill was the one branch that skipped it, so the order
stayed `PARTIALLY_FILLED` forever, holding the reservation for a quantity nothing
would execute. `LiquidityCappedFill` (v2.2) makes partial fills routine.

The remainder is now cancelled and its residual reservation released.
`Order.cancel` is legal from `PARTIALLY_FILLED` and preserves `filled_quantity`
and `average_fill_price`.

**This changes bookkeeping, not economics.** No fill is created or destroyed, so
cash, positions, realized and unrealized P&L, the equity curve, the accounting
identity and every analytics figure are exactly what they were. See
**Breaking changes**.

---

## Fixed

### The replay cursor was the last quadratic on a wired path

`ReplayState.system_events` was a tuple, and `step_one_event` appended one
`ReplayAdvanced` per record by rebuilding it. `ReplayBacktest` drives that method
once per record, so a replay of N records copied O(N²) elements — the same defect
v2.1 removed from the risk engine, v2.2 from the OMS and v2.3 from the market and
broker layers, left behind on the one path v2.2 had just wired into execution.

It survived three releases because the benchmark measured the *other* API. Its
own comment said it used the batch drain "to avoid astronomical tuple copying on
O(1M) elements" — steering around the defect rather than measuring it.

Measured, at N=2000/4000/8000 (linear is ~×2.00 per doubling):

| | N=2,000 | N=4,000 | N=8,000 | Growth |
| --- | --- | --- | --- | --- |
| v2.4.0 | 0.0165s | 0.0513s | 0.1740s | **×3.11, ×3.39** |
| v2.5.0 | 0.0089s | 0.0178s | 0.0355s | ×2.01, ×2.00 |

4.9× faster at N=8,000 and no longer growing; throughput flat at ~225,000
events/sec across all three sizes. Replay semantics are untouched: ordering,
contents, cursor behaviour and backtest/replay parity are unchanged.

### ADR-0012 said every vendor client was a stub

It was wrong about Binance, from v2.3 until now. A real HTTP transport
(`marketdata.transport.HttpTransport`, a genuine `urlopen`) and a real Binance
market-data client parsing `/api/v3/klines`, `/bookTicker`, `/trades` and
`/depth` have existed since **v1.39.0** (`5bfb6fa`), with twelve tests over
realistic payloads. It has never been run against a live endpoint from this
environment, so it is unverified — but it is not a stub.

It remains true that **no broker adapter reaches any venue**. The `integrations`
clients (Alpaca, IB, Zerodha) are canned-response stubs, and that is what
"AlphaLab does not support live trading" means.

### Documentation that disagreed with the code

- `docs/README.md` declared "Version v2.1.0" and "`ExecutionPipeline` is the only
  wired-together path" — three releases stale, and it did not mention
  `alphalab.lifecycle` at all.
- `docs/SYSTEM_DESIGN.md` listed `replay` as standalone and not invoked by
  `ExecutionPipeline`, which has been false since v2.2 (ADR-0010).

---

## Breaking changes

Confined to the simulated execution path's *bookkeeping*. No public name was
removed and no module disappeared.

### A partially filled order ends `CANCELLED`, not `PARTIALLY_FILLED`

| | v2.4.0 | v2.5.0 |
| --- | --- | --- |
| Final order status | `PARTIALLY_FILLED` | `CANCELLED` |
| `filled_quantity` / `average_fill_price` | preserved | preserved |
| `remaining_quantity` | positive, never worked | positive, recorded on a closed order |
| Membership | `active_orders` | `completed_orders` |
| Residual reservation | held indefinitely | released |
| Cash, positions, P&L, equity curve | — | **unchanged** |

Code asserting that such an order stays `PARTIALLY_FILLED` after the event that
filled it needs updating; five tests in this repository did.

A seeded run now mints one more identifier than before, because withdrawing
appends an `OrderCancelled` event. Quantities and money are unchanged, and
comparisons *between* two runs — backtest/replay parity, the deterministic
backtest regression — are unaffected.

### `SessionConfig` and `SessionState` gained fields

`SessionConfig.ordering` (defaults to `CHRONOLOGICAL`) and
`SessionState.last_record_timestamp` (defaults to `None`). Both are appended with
defaults, so positional construction is unaffected. A session fed records whose
timestamps go backwards now raises where it previously processed them and marked
the portfolio at a stale price.

### `ReplayState.system_events` is an `AppendOnlyLog`

It compares equal to the tuple it replaced, so `state.system_events == ()` still
holds and iteration is unchanged. A caller annotating the field type sees a
different one.

---

## Documentation

- `docs/ADR/0014-state-round-trip-and-the-live-data-path.md` — new; records the
  three design decisions and what was rejected.
- `docs/ADR/0012` — vendor-adapter table corrected, with the correction marked as
  such rather than quietly rewritten.
- `docs/ARCHITECTURE.md` — Implementation Status to v2.5; new sections on state
  round-trip, the live data path and partial-fill termination.
- `docs/README.md`, `docs/SYSTEM_DESIGN.md` — stale claims corrected.
- `README.md`, `ROADMAP.md` — v2.5 status and the remaining gaps.

---

## Quality gates

Measured on the release commit, not carried over:

| Gate | Result |
| --- | --- |
| `pytest -q` | **2008 passed** (1897 at v2.4.0) — 1698 unit, 109 integration, 201 regression |
| `mypy .` (strict) | clean, 915 source files |
| `ruff check .` | clean |
| `ruff format --check .` | clean, 959 files |
| `python -m build` + `twine check` | passing |

---

# [2.4.0] - 2026-09-05

## Overview

AlphaLab 2.4.0 is "Model + Strategy Lifecycle". Four packages each implemented
one stage of a lifecycle — `experiment_tracking` (v1.46.0), `model_registry`,
`research_assistant` and `deployment_manager` (all v2.0.0) — and none of them
met. Between them there was one link, `ModelVersion.run_id`, an unvalidated
string, and one convention: that a release component *might* say `"momentum@3"`,
which nothing produced and nothing parsed.

This release connects them, adds the three things that were missing at the
seams, and removes the quadratic behaviour all three stateful packages carried.

One new package, `alphalab.lifecycle`. It is an integration package, not a
fifth engine — the `alphalab.backtesting` pattern from v2.2. Each of the four
packages still works on its own and none of them changed shape to be composed.

**A deployment here is a lifecycle fact, not an operation on a machine.** It
records that an environment should be running a strategy version. It starts no
process, opens no connection and reaches no venue; AlphaLab still has no
transport to any real venue (ADR-0012 is unchanged). The registry references
artifacts and stores no bytes.

See `docs/ADR/0013-model-and-strategy-lifecycle.md`.

---

## Added

### The lifecycle package — `alphalab.lifecycle`

```text
research candidate → experiment run → validation evidence → model version
    → strategy version → promotion → deployment → rollback
```

- `LifecycleState` holds the four registries plus the evidence store, as one
  immutable, serializable value — the role `ExecutionPipelineState` plays for
  the execution path. Nothing is copied into it.
- Not to be confused with `alphalab.strategy.LifecycleState`, an enum naming
  the stages of a strategy *instance running inside a session*. A deployed
  strategy version is started and stopped many times without its stage
  changing.

### Strategy versions — `alphalab.lifecycle.strategy_version`

- `StrategyVersion` is the immutable, numbered record that did not exist.
  `studio.StrategyDefinition.version` was a free-form string, so nothing could
  be pointed at by a deployment or compared with the one before it.
- It carries the canonical `StrategyDefinition` rather than a second parameter
  format, so a candidate from `research_assistant.to_strategy_definition`
  reaches a strategy version with no shape in between.
- Four identities stay apart: the strategy line (`name`), the version, the
  `ModelRef` it runs, and the deployments it appears in. One version deployed
  to two environments is two deployments, which no field on the version could
  have said.
- `StrategyVersionRegistry` deliberately has **no** production index — see
  "one source of truth" below.

### Typed references — `alphalab.lifecycle.identity`

- `ModelRef`, `StrategyVersionRef`, `DeploymentRef`. A model version and a
  strategy version are both a name and a number, which is why they were passed
  as indistinguishable strings before; they are now different types that render
  the same way and do not compare equal.
- Rendering is `"name@version"` — the form `deployment_manager` already
  documented for release components. `parse_ref` reads it back, and a name
  containing `"@"` is refused at construction rather than producing a reference
  that parses to something else.

### Validation evidence — `alphalab.lifecycle.evidence`

- `ValidationEvidence` records what was measured, over what data, with what
  seed, and where the full report is. It computes nothing:
  `evidence_from_backtest` reads a run's `PerformanceReport` and
  `evidence_from_research` reads a `ResearchScore`. Both already existed and
  are referenced, not reimplemented.
- `evidence_id` is a SHA-256 digest of the evidence's own content — the
  construction `compute_checksum` already used for a release manifest. The same
  measurement identifies itself the same way without coordination, and
  `verify_evidence_id` detects numbers edited after the fact.
- `ValidationPolicy` states thresholds in advance. `evaluate_policy` names
  **every** failed check, not the first; a metric the policy asks for that the
  evidence does not carry is a failure, because an absent number is not a
  passing one; and evidence that no longer matches its own id fails before a
  single threshold is read.
- `required_method` lets a policy insist on `BACKTEST` evidence rather than
  numbers typed in by hand.
- **What a pass claims** is that the stated thresholds were met by the recorded
  numbers. Not statistical significance, not out-of-sample validity, and not a
  correction for how many candidates were searched first.

### The promotion gate — `alphalab.lifecycle.promotion`

- `promote_strategy_version` requires passing evidence, and requires the model
  version the strategy runs to be staged itself: a strategy cannot be more
  validated than the model inside it.
- It refuses to reach `PRODUCTION` at all. A strategy version goes live by
  being deployed, so the ledger is the only thing that ever puts one there.
- `retire_strategy_version` refuses to archive a version that is still active
  somewhere — taking down what is live is a rollback or a replacement, not a
  stage edit.
- Every accepted move appends a `StrategyPromotionRecord` carrying *why*, so a
  promoted version records what it passed rather than only that it passed.

### Checked references — `alphalab.lifecycle.registration`

- `register_model_version` and `register_strategy` verify that a cited
  experiment run exists and has **completed**, and that a cited model version is
  real. `ModelVersion.run_id` was an unvalidated `str | None`; neither package
  could check it alone without depending on the other.

### Deployment and rollback — `alphalab.lifecycle.deployment`

- `deploy_strategy_version` builds the release manifest from the version's typed
  references, makes it active in an environment, moves the version to
  `PRODUCTION`, and archives whichever version it displaced — unless that
  version is still active in another environment.
- One release package stands for one strategy version however many environments
  it reaches. A strategy version is immutable, so its manifest is fixed.
- `rollback_environment` restores the version the append-only ledger names as
  previously active, archives the one being taken down, and re-derives the
  model's deployment note. Deterministic: the same state and environment always
  roll back to the same place.
- Redeploying the version already running in an environment is refused, and
  nothing is registered before the refusal.

### Artifact references — `alphalab.model_registry.ArtifactRef`

- Where a version's trained bytes live, what they should hash to, and how big
  they are. AlphaLab never reads, writes or hashes those bytes: there is no
  object store here and this release does not pretend there is.
- `ModelVersion.__serializable__` projects a version to its metadata plus that
  reference, dropping the in-memory `model` object. An arbitrary object has no
  deterministic JSON form, and stringifying it would produce a payload that
  reads back as prose — the failure v2.1 removed from the append-only logs.

### Single-pass mapping views — `PersistentMap.items()` / `.values()`

`Mapping`'s default views iterate the keys and then index the map, resolving
every key twice. On a persistent map a resolution is a chain probe, not a hash
lookup, so the second one is real work. Iterating a 500-entry map's values is
1.7× faster, for every `PersistentMap` in the repository.

### The declared stage transitions — `alphalab.model_registry.stages`

- `LEGAL_TRANSITIONS` states every legal move once. `illegal_stage_move` is the
  pure table check, shared with `alphalab.lifecycle` so the table is not written
  twice.

### Benchmarks

- `benchmarks/benchmark_lifecycle.py` — three growth shapes (many versions of
  one line, many lines, a deep environment ledger) plus a full
  promote → deploy → rollback sweep.

---

## Changed

### One source of truth for what is live

`model_registry.DeploymentMetadata` was a hand-set blob claiming a version was
deployed somewhere; `deployment_manager`'s ledger recorded what actually was.
Both remain, but the integrated path now **derives** the note from the
deployment that happened rather than letting a caller assert one, and updates it
on rollback. `alphalab.lifecycle.views` answers "what is running here?" by
reading the ledger.

### `ParamValue` has one definition

`experiment_tracking.tracker.ParamValue` and `model_registry.registry.ParamValue`
were identical, and the registry's own docstring said consolidating them was
owed. Both now re-export `alphalab.common.types.ParamValue`. `MetadataValue` is
deliberately *not* the same alias: metadata admits `None`, a parameter does not.

---

## Fixed

### The lifecycle registries were quadratic

Every write copied the whole mapping or rebuilt the whole tuple, and three write
paths scanned the data they were writing to. Measured on one machine, per
doubling of the workload — linear is ~2x:

| Operation | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `log_metric` (2k → 4k → 8k) | 0.0091 → 0.0313 → 0.1226s (**3.4x, 3.9x**) | 0.0094 → 0.0179 → 0.0357s (1.9x, 2.0x) |
| `start_run` (2k → 4k → 8k) | 0.0165 → 0.0508 → 0.1738s (**3.1x, 3.4x**) | 0.0114 → 0.0196 → 0.0428s (1.7x, 2.2x) |
| `register_model`, one name (2k → 4k → 8k) | 0.0120 → 0.0442 → 0.1592s (**3.7x, 3.6x**) | 0.0073 → 0.0145 → 0.0310s (2.0x, 2.2x) |
| `register_model`, many names (2k → 4k → 8k) | 0.0113 → 0.0371 → 0.1341s (**3.3x, 3.6x**) | 0.0079 → 0.0190 → 0.0374s (2.4x, 2.0x) |
| `promote` (1k → 2k → 4k) | 0.0525 → 0.2000 → 0.7769s (**3.8x, 3.9x**) | 0.0126 → 0.0251 → 0.0543s (2.0x, 2.2x) |
| `register_release` (1k → 2k → 4k) | 0.0041 → 0.0124 → 0.0423s (**3.0x, 3.4x**) | 0.0044 → 0.0089 → 0.0181s (2.0x, 2.0x) |
| `deploy` (0.5k → 1k → 2k) | 0.0077 → 0.0261 → 0.0975s (**3.4x, 3.7x**) | 0.0056 → 0.0109 → 0.0222s (2.0x, 2.0x) |

The containers are the ones v2.2 introduced: `PersistentMap` where a key is
rewritten, `AppendOnlyLog` where a history only grows.

End to end on the benchmark suites, v2.3.0 → this release:

| Benchmark | v2.3.0 | v2.4.0 | Change |
| --- | --- | --- | --- |
| `benchmark_model_registry` rollback + re-promote (1k) | 3.45s / 290 per sec | 0.03s / 30,301 per sec | **104×** |
| `benchmark_deployment_manager` `active_release` (50k) | 2.99s / 16,727 per sec | 0.03s / 1,523,693 per sec | **91×** |
| `benchmark_model_registry` `production_version` (50k) | 1.78s / 28,038 per sec | 0.03s / 1,923,545 per sec | **69×** |
| `benchmark_model_registry` promote (2k over 20k versions) | 2.68s / 746 per sec | 0.04s / 50,456 per sec | **66×** |
| `benchmark_deployment_manager` rollback + re-deploy (1k) | 1.24s / 1,618 per sec | 0.03s / 73,482 per sec | **45×** |
| `benchmark_deployment_manager` deploy (5k) | 0.52s / 9,549 per sec | 0.03s / 154,437 per sec | **16×** |
| `benchmark_model_registry` `register_model` (20k) | 0.97s / 20,673 per sec | 0.08s / 248,404 per sec | **12×** |
| `benchmark_deployment_manager` `register_release` (10k) | 0.25s / 39,871 per sec | 0.04s / 226,184 per sec | **5.6×** |
| `benchmark_experiment_tracking` `log_metric` (10k) | 0.19s / 52,802 per sec | 0.04s / 262,332 per sec | **5.0×** |
| `benchmark_experiment_tracking` `best_run` (10k × 501 runs) | 0.56s / 17,736 per sec | 2.48s / 4,030 per sec | **0.23× — slower** |

### The one regression: readers that scan a whole map

`best_run` compares every run in a tracker on every call, and resolving a key in
a `PersistentMap` is a chain probe rather than a hash lookup. Scanning 501 runs
ten thousand times therefore costs about 4× what it did against plain dicts.
Both are O(runs) per call; only the constant changed.

That is the trade, and it is the right way round: the writers stopped being
quadratic and the readers stayed linear. It is the same trade v2.3 documented
for market-data ingestion at universe 1, one order of magnitude further along.

`ExperimentRun.metrics` in particular is a persistent map and not a plain dict
because a run has **two** growth axes: the values logged to a metric, and the
number of distinct metric names. A run logging a value per feature, per asset
or per layer has thousands of the latter. A draft of this release made `metrics`
a dict to buy back 25% of the `best_run` constant, and made logging distinct
metric names quadratic (3.8× per doubling) to do it. The regression test now
holds both axes.

`PersistentMap.items()` and `.values()` are new, and recover part of it.
`Mapping`'s default views resolve every key twice — once to decide it is present
and once to read it — which on a persistent map means two chain probes.
Iterating a 500-entry map's values is 1.7× faster as a result, for every reader
in the repository, not only these.

Three scans needed indexes, which no container change would have fixed:

- `promote()` called `production_version()`, which scanned every version of the
  model. `ModelRegistry` now carries `production` (name → current production
  version) and `production_line` (name → the versions that have held it, in
  order). Both are O(1).
- `rollback()` rebuilt the filtered promotion history to find the version to
  return to. It is now `production_line[-2]`.
- `deploy()` called `active_release()`, which scanned the whole ledger
  backwards. `DeploymentManager` now indexes the same records by environment.

`tests/regression/test_lifecycle_registry_complexity.py` guards both growth axes
— versions per name *and* number of names. An early draft of this release fixed
the first and made the second 50× slower and still quadratic, by inspecting
every entry of the container inside `__post_init__`, which runs on every write.

### A promotion could move a version anywhere

`promote()` refused only a move to `NONE` and a move to the stage the version
was already in. See **Breaking changes**.

---

## Breaking changes

Confined to `alphalab.model_registry` and `alphalab.experiment_tracking`. No
public name was removed, no module disappeared, and no enum member was removed.

### Two stage transitions are now refused

| Move | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `PRODUCTION → STAGING` | allowed | **refused** |
| `ARCHIVED → PRODUCTION`, version never in production | allowed | **refused** |
| `ARCHIVED → PRODUCTION`, version is the rollback target | allowed | allowed |
| `NONE → PRODUCTION` | allowed | allowed |
| `ARCHIVED → STAGING` | allowed | allowed |

A live version leaves production by being archived or replaced; a quiet
demotion leaves the model with nothing live and no record that anything was
taken down. An archived version that was never live returning to production is a
resurrection, not a restore — and if it were allowed, "roll back" would stop
being a distinguishable operation.

`NONE → PRODUCTION` stays legal at the registry level deliberately: the registry
is mechanism, and requiring evidence is policy, which lives in
`alphalab.lifecycle`.

### Container types on three state classes

Fields that were `dict` / `tuple` are now persistent containers. Both compare
equal to what they replaced — `run.metrics["loss"] == (0.5, 0.3)` and
`registry.promotions == ()` still hold — and `__post_init__` converts a
hand-built plain mapping, so runtime construction keeps working. A caller that
*annotates* one of these field types sees a different one.

| Field | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `ExperimentTracker.runs` | `Mapping[str, ExperimentRun]` | `PersistentMap[str, ExperimentRun]` |
| `ExperimentRun.metrics` | `Mapping[str, tuple[float, ...]]` | `PersistentMap[str, AppendOnlyLog[float]]` |
| `ModelRegistry.versions` | `Mapping[str, tuple[ModelVersion, ...]]` | `PersistentMap[str, PersistentMap[int, ModelVersion]]` |
| `ModelRegistry.promotions` | `tuple[PromotionRecord, ...]` | `AppendOnlyLog[PromotionRecord]` |
| `DeploymentManager.releases` | `Mapping[str, tuple[ReleasePackage, ...]]` | `PersistentMap[str, AppendOnlyLog[ReleasePackage]]` |
| `DeploymentManager.deployments` | `tuple[DeploymentRecord, ...]` | `AppendOnlyLog[DeploymentRecord]` |

**`ModelRegistry.versions[name]` changed indexing.** It was a positional tuple,
so `registry.versions["alpha"][0]` was version 1; it is now keyed by version
number, so that expression raises `KeyError` and `registry.versions["alpha"][1]`
is version 1. Use `list_versions(registry, name)` — the documented accessor,
whose return type is unchanged — or `get_version(registry, name, version)`,
which is now O(1).

### New fields on two state classes

`ModelRegistry` gained `production` and `production_line`; `DeploymentManager`
gained `environments`. They are indexes, maintained by their packages' own
functions the way `oms.book.OrderBook` maintains its own. Positional
construction of either class is unaffected (the new fields are appended and
default to empty), but a registry built by hand from `versions` alone will
report no production version until one is promoted — build through
`register_model` and `promote`.

---

## Documentation

- `docs/ADR/0013-model-and-strategy-lifecycle.md` — new.
- `docs/ARCHITECTURE.md` — Implementation Status updated to v2.4; the four
  lifecycle packages move out of the standalone list.
- `README.md`, `ROADMAP.md` — v2.4 status, capabilities and remaining gaps.
- `examples/12_model_lifecycle.py` — new: a research candidate through a real
  backtest to a deployment and back, including the refusal of a promotion that
  no evidence supports.

---

## Quality gates

Measured on the release commit, not carried over:

| Gate | Result |
| --- | --- |
| `pytest -q` | **1897 passed** (1756 at v2.3.0) |
| `mypy .` (strict) | clean, 905 source files |
| `ruff check .` | clean |
| `ruff format --check .` | clean, 948 files |

---

# [2.3.0] - 2026-09-05

## Overview

AlphaLab 2.3.0 is "Market Data + Broker/Live Execution". It is a connectivity
and convergence release: it establishes one canonical market-data model with an
explicit normalization boundary, one canonical broker adapter boundary, and
makes historical, replay, paper and live execution take the same canonical step
through the same engines.

It closes both items v2.2 deferred: market-data model convergence
(`data` / `market` / `marketdata` / `feed` / `live`, and the multiple `Bar`
types) and `broker` / `brokers` consolidation.

No new engine packages. Two new modules on the integrated path
(`alphalab.runtime.session`, `alphalab.runtime.broker_routing`) and three in the
market layer (`alphalab.market.record`, `.source`, `.normalization`).

**AlphaLab does not support live trading.** It supports the adapter contract a
live venue would be reached through; there is no connectivity to any real venue
in this repository. See `docs/ADR/0012` for the precise implemented /
adapter-only / absent breakdown.

See `docs/ADR/0011-canonical-market-data-model.md` and
`docs/ADR/0012-broker-boundary-and-environment-parity.md`.

---

## Added

### The market-data normalization boundary — `alphalab.market.normalization`

- `normalize_wire_quote` / `normalize_wire_trade` / `normalize_wire_bar` /
  `normalize_wire_book` lift `alphalab.data.feed` wire records into the
  canonical `alphalab.market` domain records the execution path consumes.
- Every number converts through `Decimal(str(value))`. `Decimal(0.1)` keeps the
  float's binary expansion; going through `str` keeps the number the provider
  wrote. That is what makes normalization deterministic.
- `NormalizationPolicy` supplies what the wire cannot carry (venue, currency,
  timeframe) and names an unattributed venue `"UNKNOWN"` rather than guessing.
  `SymbolMap` rewrites a provider symbol to an `asset_id`.
- Fields a wire record does not report — vwap, trade count, book order counts,
  trade direction — are documented as unreported, not invented.
- `is_stale` / `reject_stale` treat staleness as a caller decision, separate
  from validation: a stale record is well-formed, and how old is too old is a
  property of the strategy.

### The market-data adapter boundary — `alphalab.market.source`

- `MarketDataSource` yields canonical `MarketRecord`s and nothing else, so the
  execution path cannot tell a stored file from a socket.
- `SequenceSource` (finite, re-iterable, deterministic record ids),
  `OrderingGuarantee` (a source declares whether it can promise chronological
  order), `validate_ordering`.
- No provider API is modelled: no HTTP, no websockets, no vendor
  authentication, no reconnect loop.

### The canonical market record — `alphalab.market.record`

- `MarketInput` and `MarketRecord` moved here from
  `alphalab.backtesting.dataset` (re-exported unchanged), so a live feed
  adapter can produce a record without importing the backtesting package.
- `records_from_inputs` assigns deterministic, fixed-width record ids.

### Broker reconciliation — `alphalab.broker.reconciliation`

- `ExternalOrderMap` holds `oms_order_id ↔ broker_order_id` and refuses to
  rebind either direction.
- `classify_execution` is total: `APPLIED`, `DUPLICATE`, `UNKNOWN_ORDER`,
  `TERMINAL_ORDER`, `OVERFILL`, `INVALID`. Nothing is silently dropped.
- `apply_execution` is idempotent in `execution_id`; a refused fill leaves the
  state untouched.
- `ReconciliationLog` keeps every refusal, separating expected redelivery from
  a genuine break.
- `reconcile()` compares local state against a venue snapshot and produces
  `ReconciliationReport` — missing, unknown, divergent orders, divergent
  positions, cash difference. It states differences and does not resolve them.

### Trading sessions — `alphalab.runtime.session`

- `TradingSession` drives any `MarketDataSource` through
  `ExecutionPipeline.process_record`.
- `ExecutionMode` (`BACKTEST` / `REPLAY` / `PAPER` / `LIVE`) declares its own
  routing and whether its clock is moving.
- `max_market_data_age_seconds` gates stale records in a real-time session;
  skipped records are recorded with a reason rather than silently dropped.

### The venue boundary — `alphalab.runtime.broker_routing`

- `route_order` sends an accepted OMS order to a venue, with two pre-trade
  gates: never on a connection that is not `CONNECTED`, and never twice for one
  OMS order. The client order id is derived from the OMS order id, so a retry
  after a lost response addresses the same order.
- `apply_broker_execution` brings a venue fill back through
  `ExecutionPipeline.apply_execution_report` — the same function a simulated
  fill uses, so OMS, portfolio, allocation and analytics are identical either
  way.
- `routable()` projects an OMS order onto `broker.adapter.OMSOrderProtocol`,
  which the real `oms.order.Order` did not actually satisfy.

### The canonical step — `alphalab.runtime.execution_pipeline`

- `ExecutionPipeline.publish_record` and `process_record`. Every environment
  takes this step; `backtesting.engine.advance` delegates to it.
- `ExecutionPipeline.apply_execution_report` applies a report that did not come
  from the simulator.
- `ExecutionRouting` (`SIMULATED` / `EXTERNAL`) on `ExecutionPipelineConfig`.
  `EXTERNAL` leaves an accepted order working, invents no fill, and keeps its
  allocation reservation held.

### Benchmarks

- `benchmarks/benchmark_market_data.py` — normalization cost per record type
  and an ingestion sweep across universe size.

---

## Changed

### Market-data models converged

- `alphalab.marketdata.feed` re-exports `alphalab.data.feed`'s `Quote`, `Trade`,
  `Bar`, `OrderBookLevel` and `OrderBook`. They were field-for-field identical
  copies; they are now the same class objects.
- `alphalab.live.message.OrderBookLevel` is `alphalab.data.feed.OrderBookLevel`.
- `data.Bar` and `market.Bar` both remain, deliberately — different layers, not
  a duplicate. `tests/regression/test_market_model_convergence.py` asserts the
  distinction.

### Broker models converged

- `alphalab.brokers` routes the canonical types from `alphalab.broker`:
  `BrokerOrder`, `BrokerExecution` (`ExecutionReport`), `BrokerAccount`
  (`AccountSnapshot`), `BrokerPosition` (`PositionSnapshot`),
  `BrokerOrderStatus` (`OrderStatus`), and `AssetClass`, which was a fifth copy
  of `core.enums.AssetType`.
- `BrokerProtocol` covers order status, execution reception, account and
  positions. `PaperBroker` implements all of it.
- `ConnectionStatus` gains `RECONNECTING` and `FAILED`, which call for
  different behaviour: hold orders versus refuse them.
- `BrokerOrderStatus` gains `SUBMITTED` from the connector package, so
  broker-local operational states are one shared set.
- The routing events in `alphalab.brokers.events` name their identifier
  `broker_order_id` rather than `order_id`. `OrderManager` always passed the
  venue handle into that field, so only the name changes — but leaving it
  called `order_id` would have preserved, inside the converged package, exactly
  the ambiguity that decided which broker order model was canonical.

### Moved (all re-exported, no import breaks)

- `id_scope` / `id_source` → `alphalab.common.ids`, so a session can mint
  reproducible identifiers without importing the backtesting engine.
- `UnsupportedRecordError` → `alphalab.market.exceptions`. It is no longer a
  subclass of `BacktestError`; four environments publish records now.

---

## Fixed

### The broker layer was quadratic

`BrokerState` and `BrokerConnectorState` rebuilt their order, execution,
position and account indexes with `dict(old)` and grew `events` with
`(*events, e)` on every transition, so a session copied O(N²). v2.3 routes
paper and live execution through those states, which would have made this the
slowest part of a long session.

Measured, v2.2.0 → this release:

| Benchmark | v2.2.0 | v2.3.0 | Change |
| --- | --- | --- | --- |
| `benchmark_broker` (100k orders) | 676.70s / 148 per sec | 4.65s / 21,485 per sec | **145×** |
| `benchmark_live` (100k ticks) | 219.02s / 457 per sec | 1.28s / 78,338 per sec | **172×** |
| `benchmark_feed` (100k events) | 42.95s / 2,328 per sec | 0.69s / 144,489 per sec | **62×** |
| `benchmark_brokers` (10k cycles) | 2.44s / 4,106 per sec | 0.34s / 29,465 per sec | **7.2×** |
| `benchmark_marketdata` (100k trades) | did not run | 0.57s / 174,454 per sec | — |
| `benchmarks_market_engine` (quotes) | 105,670 per sec | 147,727 per sec | 1.4× |

`MarketState`, `MarketDataState`, `LiveState`, `FeedState` and
`MarketDataCache` moved to the same persistent containers.

### Market-data ingestion cost scaled with the universe

`MarketEngine.publish_*` rebuilt the whole `latest_*` index on every publish,
so a publish cost O(universe): 20k quotes into a 20,000-instrument universe ran
at 22,688 per sec against 215,808 per sec into a one-instrument universe, a
9.5× penalty that grew with the universe. Ingestion is now flat — ~195,000 per
sec at every universe size measured, 1 through 20,000.

The one regression is ~8% at universe 1, where a persistent map costs more than
copying a one-key dict. That is the trade, and it is the right way round.

### `benchmark_marketdata.py` could not run

It connected through `YahooAdapter`, whose client raises `NotImplementedError`
because it used to return hardcoded fake data. It fails identically on v2.2.0.
It now uses an explicit in-benchmark test double, which is what a benchmark of
the *engine* should have depended on. No vendor connectivity is faked.

### `oms.order.Order` did not satisfy `broker.adapter.OMSOrderProtocol`

Its `order_id` is an `OrderId`, not a string, and it has no single `price`. The
protocol claimed to decouple the broker layer from the OMS while not actually
matching it. `broker_routing.routable()` does the translation explicitly, at
the adapter boundary where it belongs.

---

## Breaking changes

Confined to `alphalab.brokers`, whose types are now the canonical ones. No
public name was removed from any package, no module disappeared, and no enum
member was removed — the breaks below are all changes of *shape*, not of
availability.

### Dataclass shapes

- `AccountSnapshot` takes `cash` / `equity` / `available_funds` instead of
  `cash_balance`, and requires the fields a venue account actually reports.
  `broker_id` and `metadata` are optional.
- `ExecutionReport` and `BrokerOrder` name their order field
  `broker_order_id`; `ExecutionReport.account_id` moved after `timestamp` and
  now defaults. `BrokerOrder` additionally requires `oms_order_id`, because a
  single `order_id` could not say whether it held AlphaLab's identifier or the
  venue's.
- **`PositionSnapshot` changed shape, not just field membership.** It was nine
  required fields (`position_id`, `account_id`, `symbol`, `asset_class`,
  `quantity`, `average_price`, `market_price`, `unrealized_pnl`,
  `realized_pnl`); it is now six required plus three defaulted:

  | | v2.2.0 | v2.3.0 |
  | --- | --- | --- |
  | `position_id` | required | **removed** — it restated the `"<account_id>:<symbol>"` key the state already stores the position under |
  | `market_value` | absent | **required (new)** |
  | `symbol`, `quantity`, `average_price`, `unrealized_pnl`, `realized_pnl` | required | required |
  | `account_id` | required | optional, defaults to `""` |
  | `asset_class` | required | optional, defaults to `AssetType.EQUITY` |
  | `market_price` | required | optional, defaults to `Decimal("0")` |

  Because the arity and the order both changed, **positional construction
  breaks**: a v2.2 call passing nine positional arguments will raise, and a
  call passing six will silently bind them to different fields. Construct with
  keywords. `market_price` and `market_value` are both kept because a venue
  reports both and they are different numbers — the mark for one unit, and the
  mark for the whole holding.

### Keyword-parameter renames — `order_id` → `broker_order_id`

Positional callers are unaffected; keyword callers break. Every affected public
entry point:

| Callable | v2.2.0 | v2.3.0 |
| --- | --- | --- |
| `BrokerConnectorEngine.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `OrderManager.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `brokers.list_executions` | `(state, order_id)` | `(state, broker_order_id)` |
| `brokers.validate_execution` | `(state, execution_id, order_id)` | `(state, execution_id, broker_order_id)` |
| `brokers.validate_order_cancellation` | `(state, order_id)` | `(state, broker_order_id)` |

The routing events in `alphalab.brokers.events` are renamed for the same
reason: `OrderSubmitted`, `OrderCancelled`, `OrderFilled` and
`ExecutionReceived` name their identifier `broker_order_id` instead of
`order_id`. The *value* was always the venue handle — `OrderManager` has always
passed `order.broker_order_id` — so only the name changes, and field order is
unchanged, leaving positional construction working. Nothing in the codebase
read the field by name.

### Enum values

Both enums are now aliases of canonical types, so their `.value` changed.
**`.name` is unchanged in every case**, and nothing in AlphaLab reads `.value`
on either — but external code that persisted or transmitted a `.value` will
read it back differently.

| Member | v2.2.0 `.value` | v2.3.0 `.value` | `.name` |
| --- | --- | --- | --- |
| `AssetClass.EQUITY` | `1` (`Enum`, `auto()`) | `"equity"` (`StrEnum`) | unchanged |
| `AssetClass.FUTURE` / `OPTION` / `FOREX` / `CRYPTO` | `2`–`5` | `"future"` / `"option"` / `"forex"` / `"crypto"` | unchanged |
| `OrderStatus.SUBMITTED` | `1` | `2` | unchanged |

`AssetClass` is now `alphalab.core.enums.AssetType` and therefore also gains a
`CASH` member; `OrderStatus` is now `alphalab.broker.order.BrokerOrderStatus`
and gains `PENDING_SUBMIT` (which takes value `1`, shifting `SUBMITTED` to `2`)
and `PENDING_CANCEL`. Both are widenings: no member was removed.

### Protocols and state containers

- `BrokerProtocol` (both packages) gained methods; a v2.2 adapter will not
  satisfy the v2.3 protocol.
- `MarketState`, `BrokerState`, `BrokerConnectorState`, `MarketDataState`,
  `LiveState` and `FeedState` fields are `PersistentMap` / `AppendOnlyLog`, not
  `dict` / `tuple`. Reads are unchanged (both are `Mapping` / `Sequence`);
  constructing one with a plain `dict` is a type error.

Every assertion in the existing tests is preserved. Only construction sites
moved.

---

## Quality

- 1737 tests pass (1599 at v2.2.0).
- `ruff check`, `ruff format --check`, `mypy --strict` clean.
- `python -m build` and `twine check dist/*` clean.
- All 11 examples run.
- 47 of 48 benchmarks run. `benchmark_workbench.py` fails on an unrelated
  workbench tab-lifecycle assertion and fails identically on v2.2.0; it is not
  in v2.3's scope.

---

# [2.2.0] - 2026-09-05

## Overview

AlphaLab 2.2.0 is "Unified Backtesting + Replay". It turns the existing research,
market-data, execution, portfolio and analytics components into one deterministic
dataset → analytics workflow, and closes the four v2.1 limitations that stood in
its way: the super-linear OMS order book, the leaked allocation reservation on a
risk rejection, the unserializable `OMSState`, and a replay engine that never
reached the execution path.

One new package, `alphalab.backtesting`. It is an *integration* package: it adds
no engine and no domain model, and composes `ExecutionPipeline` instead. There is
no backtest-only order model, no backtest-only fill model and — the point of the
release — no second set of portfolio books.

See `docs/ADR/0010-unified-backtesting-and-replay.md`.

---

## Added

### Unified backtesting — `alphalab.backtesting`

- `MarketDataset` / `MarketRecord` — an ordered, validated sequence of canonical
  market inputs (`Quote` / `Bar` / `Tick`), each carrying the `event_id` and
  `timestamp` `replay.HistoricalEventProtocol` requires. One dataset type feeds
  both drivers.
- `backtesting.engine.advance` — the canonical step: publish one record to the
  market engine, hand the resulting event to
  `ExecutionPipeline.process_market_event`.
- `BacktestEngine.run(config, dataset, strategy_state, context_factory)` — walks
  a dataset through that step and returns a `BacktestResult` with the final
  pipeline state, per-record `BacktestStep`s, the equity curve, the valuation and
  the compiled performance report.
- `BacktestConfig` — the execution-path config, the fill policy, the seed, and
  the analytics parameters, as one value.
- Read-only views: `final_equity`, `final_cash`, `realized_pnl`,
  `unrealized_pnl`, `commission_paid`, `equity_values`, `submitted_orders`,
  `executed_fills`, `steps_with_fills`, `performance_report`.

### Replay on the execution path — `alphalab.backtesting.replay`

- `ReplayBacktest.run(...)` drives `ReplayEngine`'s cursor and calls the *same*
  `advance` for every event it yields, so backtest/replay parity is structural
  rather than a coincidence the tests happen to observe.
- `alphalab.replay` itself is unchanged in responsibility: it still owns the
  cursor, the replay clock, the session lifecycle and chronological validation.
- The replay clock is the record index, not wall time, so a replay's own state is
  a pure function of its dataset.

### Execution semantics — `alphalab.execution.policy`

- `FillPolicy` decides one order's outcome at one market event from a
  `LiquidityContext` (asset, side, requested quantity, event price, size shown)
  and returns a `FillDecision`.
- `ImmediateFill` (default, fills in full), `StaticFill` (the pre-v2.2 fixed
  `fill_status` argument, as a policy) and `LiquidityCappedFill` (fills up to a
  share of the size the event showed; partial when capped, no fill when it showed
  none).
- `ExecutionPipeline.process_market_event` / `process_quote` gained an optional
  `fill_policy` parameter, which takes precedence over `fill_status` /
  `fill_quantity`. Both previous arguments still work unchanged.

### Persistent containers — `alphalab.common.persistent_map`

- `PersistentMap` and `PersistentSet`: immutable `Mapping` / `Set` with O(1)
  amortized update and structural sharing, using the same "shared append-only
  storage plus copy on branch" idiom `AppendOnlyLog` established in v2.1. Older
  versions keep observing exactly what they observed before; iteration is in
  first-insertion order.

### Deterministic identifiers — `alphalab.common.ids`

- `DeterministicIdSource(seed)` and `use_id_source(source)` scope where
  identifiers come from. `BacktestConfig.seed` installs one for a run and records
  it on the result.
- All identifier factories on the execution path now route through `new_id()`,
  `alphalab.core.ids.new_uuid` included.

### OMS snapshots — `alphalab.oms.snapshot`

- `capture` / `restore` / `from_primitives`, plus `OMSSnapshot` and
  `OMSEventRecord`.
- `alphalab.common.serialization` recognises a `__serializable__()` projection,
  which is how a type whose in-memory shape has no JSON form declares one.

### Benchmarks

- `benchmarks/benchmark_backtesting.py` — backtest and replay throughput, the
  scaling factor across a 4x workload, the replay cursor's overhead, and a parity
  assertion at both sizes.

---

## Fixed

### The OMS order book was quadratic

`OrderBook.add` rebuilt the whole order `dict` and both index `frozenset`s,
`OrderBook.replace` rebuilt the order `dict`, and `OMSEngine._update_sets`
rebuilt both order-id `frozenset`s — once per stored order, and the OMS stores an
order on submit and again on every lifecycle transition. Submitting N orders
copied O(N²) entries. All five containers are now persistent.

### The execution report index was quadratic too

Found by running the whole benchmark suite after the order-book fix, which is
the only reason it was found at all: `benchmarks_execution.py` took 85s for
100k fills. `ExecutionEngine.execute` and `partial_fill` stored a report by
rebuilding the whole `ExecutionState.reports` dict -- the same defect as the
order book, on the same execution path, paid by every fill a backtest produces.
`ExecutionState.reports` is now a `PersistentMap`; it is still an immutable
`Mapping` keyed by execution id and still serializes as the JSON object it
always did.

### Measured

On the development machine, full history retained:

| Benchmark | v2.1 | v2.2 |
| --- | --- | --- |
| `benchmark_oms` (100k order lifecycles) | 26.3 min | 6.7s |
| `benchmark_oms` scaling (10k → 20k) | 4.70x | 2.06x |
| `benchmarks_execution` (100k fills) | 85.3s | 1.55s |
| `benchmark_execution_pipeline` (4000 events) | 1.79s | 1.03s |
| `benchmark_execution_pipeline` scaling (4x workload) | ~7.4x | ~4.4x |

The residual above 4.00x in the pipeline benchmark is the cyclic garbage
collector walking a growing live heap, not an algorithmic term: with the
collector paused the same path scales 2.06x and 2.08x per doubling.

### A risk-rejected request leaked its allocation reservation

`AllocationEngine.allocate` commits capital against every request it emits.
A request that risk refused was skipped with a bare `continue`, and a request
with no market price was skipped earlier still; neither released anything, so
`notional_allocated` over-reported for the rest of the run.

`AllocationState.reservations` is now a per-order ledger. The allocation engine
owns the amount, the pipeline owns the moment, and releasing an order that holds
no live reservation raises `UnknownReservationError` instead of silently
subtracting — which is what makes "released exactly once" checkable.

### `OMSState` could not be serialized as a whole state

`OrderBook` keys orders by `OrderId`, a dataclass, which JSON cannot use as an
object key. Rather than weakening the identifier, the state now declares an
explicit projection: orders serialize as an array in submission order, the
derived indices are omitted and rebuilt on restore, and every event carries an
`event_type` tag so the log reads back as typed events.
`restore(from_primitives(deserialize(serialize(state)))) == state`, and the
restored state is a working state the engine carries on from. Values without such
a projection are still rejected by the encoder rather than stringified.

### Replay produced nothing

`alphalab.replay` sequenced events and never reached a strategy, an order or a
portfolio. It now drives the real path through `alphalab.backtesting.replay`.

---

## Changed

- **Breaking:** `AllocationEngine.release_reservation(state, order_id, timestamp)`
  no longer takes the amount to release — the ledger owns it.
- `OMSState.active_orders` / `completed_orders` are `PersistentSet[OrderId]`
  rather than `frozenset[OrderId]`. They compare equal to a `frozenset` in both
  directions and support `in`, `len` and iteration as before; iteration is now in
  insertion order rather than hash order.
- `OrderBook.orders()` and `orders_for_asset` / `orders_for_strategy` return
  orders in submission order.
- `AllocationState` gained `reservations`; `AllocationEngine` gained
  `reserved_notional`, and `alphalab.allocation` gained the `reserved_for_order`
  and `open_reservations` views.
- `benchmarks/benchmark_oms.py` reports a scaling factor and fails if it exceeds
  3.00x. It and `tests/regression/test_oms_book_complexity.py` pause the cyclic
  collector around their timed sections, because otherwise the growth ratio
  measures the collector rather than the data structure.
- `benchmark_execution_pipeline`'s scaling ceiling tightened from 12.0x to 6.0x.

---

## Tests

1599 tests pass (1429 on v2.1.0). New:

| Area | File |
| --- | --- |
| Persistent containers | `tests/unit/common/test_persistent_map.py` |
| Reservation ledger | `tests/unit/allocation/test_reservations.py` |
| Fill policies | `tests/unit/execution/test_fill_policy.py` |
| OMS snapshots | `tests/unit/oms/test_oms_snapshot.py` |
| Dataset validation | `tests/unit/backtesting/test_dataset.py` |
| Backtest loop | `tests/unit/backtesting/test_engine.py` |
| OMS complexity | `tests/regression/test_oms_book_complexity.py` |
| Execution report index complexity | `tests/regression/test_execution_reports_complexity.py` |
| Reservation leak | `tests/regression/test_risk_reservation_leak.py` |
| Whole-state OMS serialization | `tests/regression/test_oms_state_snapshot.py` |
| Run-to-run determinism | `tests/regression/test_deterministic_backtest.py` |
| Full backtest path | `tests/integration/test_backtest_pipeline.py` |
| Backtest/replay parity | `tests/integration/test_backtest_replay_parity.py` |

`tests/regression/test_state_serialization.py` no longer pins `OMSState` as
unserializable; it pins the fix, and that a raw dataclass-keyed mapping is still
rejected.

---

## Not in this release

Deferred to v2.3: market-data model convergence (`data` / `marketdata` / `feed`,
and the three separate `Bar` types), `broker` / `brokers` consolidation, and live
broker connectivity into the execution path. See `docs/ARCHITECTURE.md`,
"Known gaps and deferred areas".

---

# [2.1.0] - 2026-09-04

## Overview

AlphaLab 2.1.0 is "Execution + Portfolio Correctness". It makes the existing
execution spine correct and fast rather than adding new packages: mark-to-market,
a single monetary precision policy with an exact accounting identity, explicit
execution invariants, correct persistence of engine histories, and the fix for
the O(N^2) event accumulation that stopped the risk benchmark from completing.

No new packages. No new domain models. `PortfolioState` remains the single
canonical portfolio state and `oms.order.Order` the single lifecycle order.

---

## Breaking Changes

Public symbols or behaviour changed. Import sites and callers may need updating.

- **Engine histories are `AppendOnlyLog`, not `tuple`.** `RiskState`,
  `MarketState`, `ExecutionState`, `OMSState`, `AllocationState`,
  `PortfolioState`, `TransactionLedger` and the `ExecutionPipelineState`
  accumulators now hold `alphalab.common.AppendOnlyLog`. It is an immutable
  `Sequence` and compares equal to tuples and lists, so `len()`, indexing,
  slicing, iteration, `in`, `reversed()` and `== (...)` are unchanged. Code that
  required a literal `tuple` (`isinstance(..., tuple)`, concatenation with `+`)
  must call `.to_tuple()`.
- **`PortfolioEngine.apply_fill` rejects malformed fills** with
  `InvalidTransactionError`: non-positive price, negative commission, and a
  quantity that is zero or rounds to zero at `SHARE_QUANT`. These previously
  produced an incoherent position, a fabricated `PositionClosed` event, or a
  ledger entry for a trade that did not happen.
- **Every non-trading execution outcome is terminal for the order.** A
  rejected, expired or unfilled execution moves the OMS order to `REJECTED` /
  `EXPIRED` / `CANCELLED` and out of `active_orders`; previously it stayed
  `ACCEPTED` and open forever. `Order.reject` accordingly accepts `ACCEPTED` in
  addition to `NEW` / `PENDING`; an order that has already traded still cannot
  be rejected.
- **One portfolio snapshot per market event**, not one per fill.
  `ExecutionPipelineState.portfolio_snapshots` now also has a point for events
  that only marked the book and did not trade.
- **`DeterministicEncoder` no longer stringifies unknown objects.** `Decimal`,
  dataclasses, `AppendOnlyLog`, `Enum` and `UUID` are handled by explicit
  branch; anything else raises `SerializationError` naming the type. Callers
  that relied on the previous silent `str()` fallback were receiving payloads
  that could not be read back.
- **`AnalyticsEngine.compile_report`, `AllocationEngine.allocate` and
  `calculate_attribution`** accept `Sequence` where they previously required
  `tuple`. Existing tuple callers are unaffected.

---

## Added

- **Mark-to-market.** `PortfolioEngine.update_market_prices` is wired into
  `ExecutionPipeline.process_market_event` and runs *before* any decision is
  taken on the event, so unrealized P&L and NAV reflect the current market and
  the risk state is resynced from the marked book. It moves unrealized P&L only
  -- cash, realized P&L, commissions and the ledger are untouched. Non-positive
  prices are rejected as invalid market data and unheld assets ignored; a
  position with no price keeps its previous mark. Emits `MarketValueUpdated`
  when something was actually re-marked.

  The marked portfolio reaches **risk** only. The strategy's `StrategyContext`
  comes from the caller's `context_factory`, which the pipeline does not
  populate, and allocation sizes from market prices and its capital budget.
- **`PortfolioValuation.snapshot` / `PortfolioValuationSnapshot`** -- the
  deterministic read model over `PortfolioState`: cash, long/short/positions
  value, unrealized and realized P&L, commissions, and equity. Carried on
  `ExecutionPipelineResult.valuation` and projected into the analytics
  `PortfolioSnapshot`. Not a second portfolio state.
- **`PortfolioState.realized_pnl` and `PortfolioState.commission_paid`** --
  cumulative account totals that survive a position being closed and dropped
  from `positions`.
- **`alphalab.portfolio.money`** -- the portfolio's single monetary precision
  policy (see *Monetary precision* below), and **`Position.cost_basis`**, the
  authoritative money figure it rests on.
- **`ExecutionPipelineResult.unpriced_requests`** -- order requests dropped
  because the pipeline had no market price for the asset.
- **`alphalab.common.AppendOnlyLog`** -- immutable append-only sequence with
  O(1) amortized append and copy-on-branch structural sharing.
- **`benchmarks/benchmark_execution_pipeline.py`** -- end-to-end pipeline
  throughput and scaling benchmark.
- Tests: `tests/unit/common/test_append_log.py`,
  `tests/unit/portfolio/test_portfolio_invariants.py`,
  `tests/unit/portfolio/test_monetary_precision.py`,
  `tests/integration/test_mark_to_market_pipeline.py`,
  `tests/regression/test_event_accumulation_complexity.py`,
  `tests/regression/test_state_serialization.py`.

---

## Monetary precision

`alphalab.portfolio.money` holds one rounding policy for the whole portfolio:

1. **Money is exact at the currency minor unit.** Every monetary amount stored
   in `PortfolioState` -- cash, cost basis, realized P&L, commissions, market
   value -- is an exact multiple of `0.01`. `to_money` is the only place
   rounding happens.
2. **Rounding happens once, at entry.** `PortfolioEngine.apply_fill` rounds the
   fill's notional and commission as they enter, and both the cash movement and
   the position's cost basis are derived from those same rounded values.
3. **Prices and quantities are inputs, not money.** They keep their own finer
   precision (`PRICE_QUANT` 1e-4, `SHARE_QUANT` 1e-6).

`Position.cost_basis` is the authoritative money figure -- the exact cash paid
(long) or received (short) for the open quantity. Realized P&L is the difference
between money in and money out; unrealized P&L is `market_value - basis`.
Splits (partial close, reversal) round one part and derive the other by
subtraction, so the parts always sum to the exact whole. `average_cost` is
derived from the basis and keeps its meaning; a `Position` constructed without a
`cost_basis` derives one from `average_cost * |quantity|`.

The accounting identity is therefore **exact** -- an identity over exact Decimal
values for any price and quantity the engine accepts, not an approximation that
happens to hold for round numbers:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

---

## Fixed

- **O(N^2) event/history accumulation.** Every engine grew its append-only
  history with `(*state.events, event)`, rebuilding the whole tuple on each
  transition; N transitions copied O(N^2) elements. Measured on the development
  machine, with full history retained in every case:

  | Benchmark | v2.0.0 | v2.1.0 |
  | --- | --- | --- |
  | `benchmark_risk_engine` (100k evaluations) | 285.7s | **1.4s** |
  | `benchmarks_market_engine` (100k quotes) | 2,060 ops/sec | **~167,000 ops/sec** |
  | `benchmarks_market_engine` (100k books) | 640 ops/sec | **~165,000 ops/sec** |
  | `benchmark_portfolio_engine` (20k fills) | 1.90s | **0.22s** |
  | `benchmark_execution_pipeline` (4000 events) | 6.76s | **~1.8s** |

  `benchmark_risk_engine` was 9.5x outside its own 30s budget on v2.0.0.
  `benchmark_portfolio_engine`'s full 100k-fill workload could not complete on
  v2.0.0 at all; it now runs in ~1.9s. End-to-end, the pipeline benchmark's
  scaling across a 4x workload dropped from ~17x to ~7.5x.
- **The accounting identity was not exact.** The cash ledger rounded
  `quantity * price + commission` while the position independently rounded
  `(exit_price - average_cost) * quantity`. Two roundings of one economic event
  disagreed by up to half a cent each and the error accumulated: an ordinary
  penny-spread quote (bid 100.00 / ask 100.01, mid 100.005) put the identity out
  by $0.01, and randomized multi-asset portfolios drifted by up to $0.05. See
  *Monetary precision* above.
- **Append-only histories serialized as a repr string.** `dataclasses.asdict`
  recurses into tuples but deep-copies anything else, so an `AppendOnlyLog`
  reached `DeterministicEncoder`, whose `str()` fallback wrote
  `"events": "AppendOnlyLog([...])"` instead of a JSON array. It raised nothing
  and passed snapshot validation, so `PersistenceAdapter.to_snapshot` of a
  migrated state silently persisted unreadable history.
  `alphalab.common.dataclass_to_dict` now does its own recursion -- `asdict`'s
  behaviour plus one rule: an `AppendOnlyLog` converts like the tuple it
  replaced.
- **Realized P&L was discarded when a position closed.** Closing removes the
  position from `positions`, taking its `realized_pnl` with it, so account-level
  realized P&L was unrecoverable after a round trip. It now accumulates on
  `PortfolioState`.
- **Analytics trade records could be credited with another fill's P&L.**
  `ExecutionPipeline._trade_record` scanned the whole portfolio history in
  reverse for the asset's last realized-P&L event, so an opening fill inherited
  an earlier close's P&L and the performance report overstated realized P&L on
  every re-entry. It now reads only the events the current fill produced.
- **An order for an asset with no market price raised `KeyError`.** Allocation
  prices unknown assets at `0.00`; the pipeline now drops such requests before
  the OMS and reports them on `unpriced_requests`. The condition is per-event --
  a later quote makes the asset tradeable.
- **`FillStatus.NO_FILL` left the order open.** It now moves the order to
  `CANCELLED`, removes it from `active_orders`, and releases the allocation
  reservation exactly once -- fabricating no fill, trade or position.
- **Venue-rejected orders stayed open forever** in `oms.active_orders`, so open
  orders never reconciled with fills.
- **Positions were never repriced between fills**, so unrealized P&L, NAV, risk
  NAV and the equity curve were stale until the next trade.
- **`benchmarks/benchmarks_market_engine.py` could never run.** Its first quote
  carried timestamp `0.0`, which `market.timestamp.is_valid_timestamp` rejects
  as not strictly positive, so the benchmark raised `MarketValidationError`
  immediately. The sequence now starts at `1.0`. (Pre-existing on v2.0.0.)

---

## Not Changed

- The D1 close-fill cash accounting fix from 2.0.0 stands: realized P&L is still
  never added to cash on top of the trade proceeds.
- Commissions still stay out of a position's cost basis; `average_cost` remains
  a clean per-unit price.
- No package was added, removed, or merged. The standalone-engine /
  integrated-path split from ADR-0009 is unchanged.

---

## Known Limitations

- **The OMS order book is the execution path's remaining super-linear term.**
  `OrderBook.add` / `.replace` copy the whole order dict and
  `OMSEngine._update_sets` copies both order-id frozensets, once per stored
  order. This is a persistent-map problem, not event accumulation, and was
  deliberately left out of scope. `benchmarks/benchmark_execution_pipeline.py`
  measures it.
- **`OMSState` cannot be JSON-serialized as a whole state**, on 2.1.0 exactly as
  on 2.0.0: `OrderBook` keys orders by the `OrderId` dataclass, which neither
  `asdict` nor `json.dumps` accepts as a mapping key. Its history logs serialize
  correctly; the limitation is the typed identifier, not the log.
- **A risk-rejected request does not release its allocation reservation**, so
  `notional_allocated` over-reports after a risk rejection. Pre-existing; it
  does not gate trading, because the budget check reads
  `available_global_capital`.
- `dataclasses.asdict` cannot be extended, so it still returns an
  `AppendOnlyLog` for a history field. `alphalab.common.dataclass_to_dict` and
  `alphalab.persistence.serialize` are the supported serialization boundary.
- **Valuation is single-currency.** `PortfolioValuation` and `NAVCalculator`
  value the base currency only.
- **`_trade_record` still hard-codes** `sector_id="UNCLASSIFIED"` and
  `holding_period_seconds=0.0` ("D3", deferred).

---

## Quality Gates

`ruff check`, `ruff format --check`, `mypy` (strict, 843 source files), `pytest`
(1429 tests), `git diff --check`, `python -m build`, and `twine check dist/*`
all pass.

---

# [2.0.0] - 2026-09-04

## Overview

AlphaLab 2.0.0 is the v2 release line. It consolidates the v1.34.0–v1.46.0 engine
series, adds four new standalone packages, unifies the canonical execution-path
domain models, and fixes two portfolio/analytics defects found by an end-to-end
trading-research validation.

AlphaLab remains a library: there is no server, daemon, scheduler process, or CLI.
`alphalab.runtime.ExecutionPipeline` is the only spine that wires domain engines
together (market → strategy → allocation → risk → OMS → execution simulator →
portfolio → analytics); every other package is an independent, individually tested
engine that is not fused into a single runtime.

---

## Breaking Changes

Public symbols removed or changed. Import sites must be updated.

- **`alphalab.core.OrderRequest` is now the single proposed-order DTO.** The
  independent `alphalab.allocation.request.OrderRequest` /
  `alphalab.allocation.request.OrderSide` and the independent
  `alphalab.risk.models.OrderRequest` / `alphalab.risk.models.OrderSide` are
  removed. `alphalab.allocation.request` no longer exists. `alphalab.risk.models`
  now contains only `RiskViolation`. `OrderSide` is no longer part of the
  `alphalab.allocation` or `alphalab.risk` public API — use
  `alphalab.core.enums.Side`.
- **`alphalab.core.enums.Side` is the canonical order direction** everywhere on
  the execution path (`OrderRequest`, `oms.order.Order`, `Fill`, `Trade`, risk
  and allocation checks). `BUY`/positive and `SELL`/negative semantics are
  unchanged.
- **`alphalab.oms.order.Order` is the canonical lifecycle order.** The
  `alphalab.core.order` module and the `alphalab.core.Order` re-export are
  removed, along with `oms.order.Order`'s unused adapter surface
  (`to_core_order`, `from_core_order`, `canonical_order`). Every
  `oms.order.Order` field, property, and lifecycle transition is unchanged.
- **`Fill.filled_at` and `Trade.executed_at` are now `float`** (Unix seconds),
  matching every other timestamp on the execution path. They were previously
  timezone-aware `datetime`; the tz-aware `__post_init__` guard is removed. In
  `dataclasses.asdict` output these fields are now numbers, not `datetime`
  objects.
- **Removed proven-dead `alphalab.core` symbols:** `OrderCompat`, `Event`
  (`core/event.py`), `Signal` (`core/signal.py`), and the
  `alphalab.core.portfolio` / `alphalab.core.position` re-export shims. Use
  `alphalab.portfolio.engine.PortfolioState` and
  `alphalab.portfolio.position.Position` directly. `core.ids`
  (`PortfolioId` / `PositionId` / `SignalId` / `new_*`) is unaffected.
- **Package version** is `2.0.0`; the `alphalab.common.version` fallback and the
  `Development Status` classifier (`4 - Beta`) are updated to match.

---

## Added

### New packages (PR047–PR050)

- **Model Registry** (`alphalab.model_registry`) — versioned registration of
  trained model artifacts, `NONE → STAGING/PRODUCTION/ARCHIVED` promotion with an
  immutable promotions log, production rollback, and deployment metadata. Links
  to `alphalab.experiment_tracking` runs by id. State threaded functionally
  through immutable `ModelRegistry` values; no on-disk serialization.
- **AI Research Assistant** (`alphalab.research_assistant`) — deterministic,
  offline (no LLM, no network) grid-search research driver: candidate generation
  over a parameter grid, evaluation via a caller-supplied evaluator, best-first
  ranking, Markdown reporting, a one-call `run_research_workflow`, and a bridge
  that lifts a chosen candidate into an `alphalab.studio` `StrategyDefinition`.
- **Deployment Manager** (`alphalab.deployment_manager`) — packaging of
  strategy/model stacks into versioned, SHA-256-checksummed `ReleasePackage`s
  (manifest only, no artifact bytes), an append-only ledger of which release is
  active per environment, and previous-release rollback.
- **AlphaLab Enterprise** (`alphalab.enterprise`) — deterministic in-memory
  governance layer over one immutable `EnterpriseState`: session-lifecycle
  identity (no credentials accepted or stored), RBAC, append-only audit log,
  multi-user workspaces, secret *references* + rotation metadata (no secret
  values), and a compliance snapshot.

### Engine series (v1.34.0 – v1.46.0, PR034–PR046)

Delivered on `main` before the v2 line and included in 2.0.0: feature store,
factor library, options engine, futures engine, crypto engine, macro engine,
alternative data engine, machine learning engine, deep learning engine,
reinforcement learning engine, cloud research engine, cluster scheduler, and
experiment tracking. Each is a standalone deterministic package with its own
tests and benchmark.

---

## Changed — canonical execution domain models (R1–R4)

- **R1** — unified allocation/risk `Side` and `OrderRequest` into
  `alphalab.core` (see Breaking Changes). `alphalab.runtime.execution_pipeline`
  no longer converts requests field-by-field across the allocation → risk → OMS
  boundary; the `_risk_request` / `_core_side` bridges and
  `execution_adapters.core_side_from_oms` are deleted.
- **R2** — removed the five proven-dead `alphalab.core` symbols (see Breaking
  Changes). Implementations deleted outright; no aliases left behind.
- **R3** — retitled `alphalab.oms.order` as the canonical lifecycle order and
  removed its dormant adapter hooks; deleted `alphalab.core.order`. The order the
  execution pipeline produces is byte-for-byte identical.
- **R4** — `Fill.filled_at` / `Trade.executed_at` changed from `datetime` to
  `float`. `execution_adapters.canonical_execution_from_report` now passes
  `report.timestamp` straight through instead of
  `datetime.fromtimestamp(..., tz=UTC)`.

---

## Fixed

- **D1 (critical) — portfolio close/reduce cash accounting.**
  `alphalab.portfolio.engine.PortfolioEngine.apply_fill` computed
  `cash_impact = -(quantity * price) - commission + pnl`. On a closing or
  reducing fill the `+ pnl` term double-counted realized P&L into cash —
  overstating cash / NAV / `PerformanceReport.ending_capital` /
  `RiskState.current_nav` on wins and understating them on losses. The `+ pnl`
  term is removed; position cost-basis math and the
  `PositionReduced` / `PositionClosed` event payloads are unchanged. Guarded by
  new unit tests (winning, losing, and partial-reduction round-trips) and
  `tests/regression/test_close_fill_cash_accounting.py`, which drives the real
  `ExecutionPipeline` open → hold → close and asserts
  `NAV == ending_capital == risk.current_nav == starting_cash + realized_pnl - commissions`.
- **D2 (medium) — `PerformanceReport` serialization.**
  `alphalab.analytics.attribution.calculate_attribution` wrapped
  `AttributionMetrics` fields in `types.MappingProxyType`, which
  `alphalab.persistence` (via `dataclasses.asdict`) cannot copy
  (`cannot pickle 'mappingproxy' object`). It now returns ordinary `dict`s, like
  every other frozen dataclass in AlphaLab. Field names and the
  `Mapping[str, Decimal]` annotations are unchanged.

---

## Build / Packaging

- The release build and distribution validation were aligned with **Core
  Metadata 2.5 / Twine 7**. Hatchling 1.30 emits `Metadata-Version: 2.5`
  (PEP 639), which Twine 6 rejects; the dev toolchain pin is now
  `twine>=7.0,<8`, and the redundant unpinned `pip install build twine` step
  was dropped from the CI and release workflows so they use the pinned
  toolchain. `python -m build` and `twine check dist/*` pass for the 2.0.0
  wheel and sdist. Hatchling is unchanged; no application dependency changed
  (`dependencies = []`).

---

## Known gaps (unchanged in 2.0.0)

- `alphalab.runtime.execution_pipeline._trade_record` still attributes realized
  P&L by scanning portfolio events in reverse and hard-codes
  `sector_id="UNCLASSIFIED"` and `holding_period_seconds=0.0` (deferred — "D3").
- Mark-to-market position repricing is not implemented.
- `alphalab.replay` is a standalone engine; it does not drive `ExecutionPipeline`.
- `alphalab.data.feed.Bar` and `alphalab.market.bar.Bar` are separate,
  incompatible models (a third `Bar` lives in `alphalab.marketdata.feed`).
- The `broker` / `brokers`, `marketdata` / `data` / `feed`, `kernel`, and
  `core/events` areas remain intentionally unresolved / product-surface
  decisions.

---

## Quality gates

- ruff check, ruff format --check, mypy --strict (833 source files) — clean
- pytest — 1221 passed (1207 unit, 4 integration, 10 regression)
- `git diff --check` — clean

---

# [1.0.0] - 2026-07-05

## First Stable Release

AlphaLab 1.0.0 is the first stable public release of the framework.

This release establishes the core architecture for deterministic quantitative research, systematic strategy development, portfolio optimization, historical replay, production runtime management, and institutional research workflows.

---

## Added

### Core Framework

- Immutable domain models
- Deterministic engine APIs
- Event-driven architecture
- Shared validation utilities
- Shared registry utilities
- Common infrastructure package
- Python 3.12 support
- Strict static typing throughout the framework

### Research

- Research Engine
- Statistical research workflows
- Strategy evaluation
- Research payload validation

### Strategy Runtime

- Strategy lifecycle management
- Event dispatch
- Runtime supervision
- Context abstraction
- Intent validation

### Universal Data Engine

- Canonical datasets
- Dataset metadata
- Schema validation
- Data quality reporting
- Timeframe conversion
- Dataset cataloguing

### Replay Engine

- Historical event replay
- Deterministic market simulation
- Timeline reconstruction

### Portfolio Optimizer

- Capital allocation
- Equal Weight optimization
- Minimum Variance optimization
- Maximum Sharpe optimization
- Inverse Volatility optimization
- Portfolio constraints
- Exposure analysis
- Transaction cost estimation
- Portfolio rebalancing

### Broker Integrations

- Provider abstraction
- Paper Trading
- Alpaca integration architecture
- Interactive Brokers integration architecture
- Zerodha integration architecture
- Authentication workflows
- Connection management

### Production Runtime

- Runtime supervision
- Health monitoring
- Checkpointing
- Recovery workflows
- Runtime metrics

### Strategy Studio

- Project management
- Strategy registration
- Research sessions
- Pipelines
- Reports
- Workspace management
- Backtest orchestration

### AlphaLab Workbench

- Unified workspace
- Project management
- Research orchestration
- Dataset management
- Dashboard infrastructure

---

## Documentation

Added comprehensive documentation including:

- README
- Getting Started Guide
- Architecture Guide
- System Design
- Engineering Guidelines
- Architectural Decision Records (ADRs)
- Examples documentation
- Contributing Guide

---

## Examples

Added ten fully synchronized runnable examples covering:

1. Research Engine
2. Strategy Runtime
3. Replay Engine
4. Market Data
5. Broker Integrations
6. Portfolio Optimizer
7. Universal Data Engine
8. Strategy Studio
9. Workbench
10. Complete end-to-end workflow

---

## Engineering

Improved overall project quality through:

- Shared validation infrastructure
- Shared registry utilities
- Common package refactoring
- Consistent immutable APIs
- Packaging improvements
- Version alignment
- Example synchronization
- Release engineering

---

## Quality Assurance

Validated with:

- ✅ 583 passing unit tests
- ✅ Strict MyPy (631 source files)
- ✅ Ruff clean
- ✅ Python package build
- ✅ Wheel validation
- ✅ Source distribution validation
- ✅ Twine package verification

---

## Packaging

AlphaLab 1.0.0 is distributed as:

- Source Distribution (`sdist`)
- Universal Python Wheel (`py3-none-any`)

---

## Notes

This release establishes the stable architectural foundation of AlphaLab.

Future releases will expand the framework with additional quantitative research capabilities while maintaining backward compatibility wherever practical.